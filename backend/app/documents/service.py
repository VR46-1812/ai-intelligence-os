"""Vertical document-to-evidence processing service for selected catalog papers."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.catalog.identity import new_ulid
from app.config import PathSettings
from app.db import transaction
from app.documents.download import DocumentDownloadError, SafePdfDownloader
from app.documents.models import ProcessedPaper, ProcessingStatus, ProcessingSummary
from app.documents.parser import PdfParseError, PdfTextExtractor
from app.domain.models import Document, DocumentRole, ParseStatus
from app.repositories.sqlite import SQLiteRepositories


class DocumentProcessingService:
    """Acquire and parse current arXiv versions while retaining every safe failure state."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        paths: PathSettings,
        downloader: SafePdfDownloader,
        extractor: PdfTextExtractor,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = new_ulid,
    ) -> None:
        self._connection = connection
        self._repositories = repositories
        self._paths = paths
        self._downloader = downloader
        self._extractor = extractor
        self._clock = clock
        self._id_factory = id_factory

    async def process(self, *, limit: int = 5) -> ProcessingSummary:
        if not 1 <= limit <= 25:
            raise ValueError("document processing limit must be between 1 and 25")
        candidates = self._connection.execute(
            """SELECT w.id work_id, v.id version_id, v.version_label, x.normalized_value arxiv_id
            FROM works w JOIN work_versions v ON v.id=w.current_version_id AND v.is_current=1
            JOIN external_ids x ON x.work_id=w.id AND x.id_type='arxiv'
            WHERE w.work_type='paper' AND NOT EXISTS (
              SELECT 1 FROM documents d WHERE d.work_version_id=v.id
                AND d.document_role='paper_pdf' AND d.parse_status IN ('parsed','partial')
            ) ORDER BY COALESCE(v.published_at,w.first_published_at) DESC, w.id LIMIT ?""",
            (limit,),
        ).fetchall()
        results: list[ProcessedPaper] = []
        for row in candidates:
            results.append(
                await self._process_one(
                    str(row["work_id"]),
                    str(row["version_id"]),
                    str(row["arxiv_id"]),
                    str(row["version_label"]),
                )
            )
        return ProcessingSummary(
            requested=len(candidates),
            succeeded=sum(result.status is ProcessingStatus.SUCCEEDED for result in results),
            failed=sum(result.status is ProcessingStatus.FAILED for result in results),
            quarantined=sum(result.status is ProcessingStatus.QUARANTINED for result in results),
            results=tuple(results),
        )

    async def _process_one(
        self, work_id: str, version_id: str, arxiv_id: str, version_label: str
    ) -> ProcessedPaper:
        suffix = version_label if version_label.lower().startswith("v") else ""
        url = f"https://arxiv.org/pdf/{arxiv_id}{suffix}"
        try:
            downloaded = await self._downloader.download(url)
        except DocumentDownloadError as error:
            status = (
                ProcessingStatus.QUARANTINED
                if error.quarantined_path is not None
                else ProcessingStatus.FAILED
            )
            self._record_attempt(
                work_id,
                version_id,
                url,
                status,
                error.code,
                error.safe_detail,
                error.quarantined_path,
            )
            return ProcessedPaper(
                work_id=work_id, status=status, error_code=error.code, safe_detail=error.safe_detail
            )

        now = self._clock()
        document = Document(
            id=self._id_factory(),
            work_version_id=version_id,
            document_role=DocumentRole.PAPER_PDF,
            source_url=url,
            local_path=downloaded.path.relative_to(self._paths.data_root).as_posix(),
            media_type=downloaded.media_type,
            byte_size=downloaded.byte_size,
            sha256=downloaded.sha256,
            parse_status=ParseStatus.PENDING,
            acquired_at=now,
        )
        with transaction(self._connection):
            created = self._repositories.documents.create_or_get(document)
            document = created.entity
            self._insert_attempt(
                work_id, version_id, url, ProcessingStatus.SUCCEEDED, None, None, None
            )
        try:
            parsed = self._extractor.extract(downloaded.path)
        except PdfParseError as error:
            failed = document.model_copy(
                update={
                    "parser_name": self._extractor.parser_name,
                    "parser_version": "1",
                    "parse_status": ParseStatus.FAILED,
                    "parsed_at": self._clock(),
                }
            )
            with transaction(self._connection):
                self._repositories.documents.update(failed)
                self._insert_attempt(
                    work_id,
                    version_id,
                    url,
                    ProcessingStatus.FAILED,
                    error.code,
                    error.safe_detail,
                    None,
                )
            return ProcessedPaper(
                work_id=work_id,
                document_id=document.id,
                status=ProcessingStatus.FAILED,
                error_code=error.code,
                safe_detail=error.safe_detail,
            )

        status = ParseStatus.PARTIAL if parsed.partial else ParseStatus.PARSED
        with transaction(self._connection):
            for span in parsed.spans:
                self._connection.execute(
                    """INSERT OR IGNORE INTO evidence_spans
                    (id,document_id,section_path,page_start,page_end,char_start,char_end,span_text,
                     normalized_text_sha256,metadata_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self._id_factory(),
                        document.id,
                        span.section_path,
                        span.page,
                        span.page,
                        span.char_start,
                        span.char_end,
                        span.text,
                        span.normalized_sha256,
                        json.dumps(span.metadata, sort_keys=True, separators=(",", ":")),
                        self._clock().isoformat(),
                    ),
                )
            self._repositories.documents.update(
                document.model_copy(
                    update={
                        "parser_name": self._extractor.parser_name,
                        "parser_version": "1",
                        "parse_status": status,
                        "page_count": parsed.page_count,
                        "parsed_at": self._clock(),
                    }
                )
            )
            self._connection.execute(
                "UPDATE works SET lifecycle_state='parsed', updated_at=? WHERE id=?",
                (self._clock().isoformat(), work_id),
            )
        return ProcessedPaper(
            work_id=work_id,
            document_id=document.id,
            status=ProcessingStatus.SUCCEEDED,
            pages=parsed.page_count,
            evidence_spans=len(parsed.spans),
            safe_detail=(
                f"Parsed with {len(parsed.empty_pages)} empty page(s)."
                if parsed.empty_pages
                else None
            ),
        )

    def _record_attempt(
        self,
        work_id: str,
        version_id: str,
        url: str,
        status: ProcessingStatus,
        error_code: str | None,
        safe_detail: str | None,
        quarantine_path: Path | None,
    ) -> None:
        with transaction(self._connection):
            self._insert_attempt(
                work_id, version_id, url, status, error_code, safe_detail, quarantine_path
            )

    def _insert_attempt(
        self,
        work_id: str,
        version_id: str,
        url: str,
        status: ProcessingStatus,
        error_code: str | None,
        safe_detail: str | None,
        quarantine_path: Path | None,
    ) -> None:
        relative_quarantine = None
        if quarantine_path is not None:
            relative_quarantine = str(quarantine_path.relative_to(self._paths.data_root).as_posix())
        self._connection.execute(
            """INSERT INTO document_acquisition_attempts
            (id,work_id,work_version_id,source_url,status,error_code,safe_detail,quarantine_path,attempted_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                self._id_factory(),
                work_id,
                version_id,
                url,
                status.value,
                error_code,
                None if safe_detail is None else safe_detail[:500],
                relative_quarantine,
                self._clock().isoformat(),
            ),
        )
