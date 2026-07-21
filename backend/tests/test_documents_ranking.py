"""Fixture-based safe PDF, evidence, ranking, and API tests."""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.catalog.taxonomy import load_default_taxonomy
from app.config import REPOSITORY_ROOT, PathSettings, initialize_directories
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.documents.api import router as evidence_router
from app.documents.download import DocumentDownloadError, SafePdfDownloader
from app.documents.parser import PageExtractionClass, PdfParseError, PdfTextExtractor
from app.documents.service import DocumentProcessingService
from app.ranking.engine import DeterministicRankingEngine
from app.repositories import SQLiteRepositories


@pytest.fixture
def document_store() -> Iterator[tuple[PathSettings, SQLiteDatabase, sqlite3.Connection]]:
    root = REPOSITORY_ROOT / "data" / ".test-documents" / uuid4().hex
    paths = PathSettings(data_root=root)
    initialize_directories(paths)
    database = SQLiteDatabase(paths.database_path)
    MigrationRunner(database).migrate()
    connection = database.connect()
    with transaction(connection):
        connection.execute(
            """INSERT INTO sources(
            id,source_key,display_name,trust_tier,base_url,poll_interval_minutes,
            connector_version,created_at,updated_at) VALUES
            ('source','arxiv','arXiv','A','https://export.arxiv.org',60,'v1',?,?)""",
            ("2026-07-20T00:00:00Z", "2026-07-20T00:00:00Z"),
        )
        connection.execute(
            """INSERT INTO source_records(id,source_id,upstream_id,upstream_version,canonical_url,
            payload_sha256,raw_payload_path,observed_at,published_at,normalization_status)
            VALUES ('record','source','2607.00001','v1','https://arxiv.org/abs/2607.00001',
            'rawhash','raw/record.xml','2026-07-20T00:00:00Z','2026-07-19T00:00:00Z','normalized')"""
        )
        connection.execute(
            """INSERT INTO works(id,work_type,canonical_title,normalized_title,abstract,
            publication_status,first_published_at,current_version_id,lifecycle_state,created_at,updated_at)
            VALUES ('work','paper','Evidence Agents','evidence agents','A paper.','preprint',
            '2026-07-19T00:00:00Z','version','normalized','2026-07-20T00:00:00Z','2026-07-20T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO work_versions(id,work_id,version_label,title,abstract,source_record_id,
            published_at,observed_at,is_current) VALUES ('version','work','v1','Evidence Agents',
            'A paper.','record','2026-07-19T00:00:00Z','2026-07-20T00:00:00Z',1)"""
        )
        connection.execute(
            """INSERT INTO external_ids(
            id,work_id,id_type,normalized_value,raw_value,source_record_id,created_at)
            VALUES ('external','work','arxiv','2607.00001','2607.00001v1','record',
            '2026-07-20T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO topics(id,topic_key,display_name,description) VALUES
            ('topic','agentic-systems','Agentic Systems','Agents')"""
        )
        connection.execute(
            """INSERT INTO work_topics(work_id,topic_id,assignment_method,confidence,created_at)
            VALUES ('work','topic','rule',1,'2026-07-20T00:00:00Z')"""
        )
    try:
        yield paths, database, connection
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def pdf_fixture_root() -> Iterator[Path]:
    root = REPOSITORY_ROOT / "data" / ".test-pdfs" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _pdf_bytes(*, empty_page: bool = False) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((40, 60), "1 Introduction", fontsize=14)
    page.insert_textbox((40, 90, 280, 700), "Left column evidence. " * 8, fontsize=10)
    page.insert_textbox((320, 90, 560, 700), "Right column method. " * 8, fontsize=10)
    if empty_page:
        document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


def _downloader(
    client: httpx.AsyncClient, paths: PathSettings, *, maximum: int = 1_000_000
) -> SafePdfDownloader:
    return SafePdfDownloader(
        client,
        destination=paths.raw_documents_root / "pdf",
        temporary=paths.temporary_root,
        quarantine=paths.quarantine_root,
        maximum_bytes=maximum,
        chunk_bytes=4096,
        concurrency=3,
        maximum_retries=0,
    )


def test_downloader_streams_hashes_deduplicates_and_enforces_policy(
    document_store: tuple[PathSettings, SQLiteDatabase, sqlite3.Connection],
) -> None:
    paths = document_store[0]
    payload = _pdf_bytes()

    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/pdf"}, content=payload
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            first = await _downloader(client, paths).download("https://arxiv.org/pdf/2607.00001v1")
            second = await _downloader(client, paths).download("https://arxiv.org/pdf/2607.00001v1")
        assert first.path == second.path
        assert first.sha256 == hashlib.sha256(payload).hexdigest()
        assert first.path.read_bytes() == payload

        bad_transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=b"no"
            )
        )
        async with httpx.AsyncClient(transport=bad_transport) as client:
            with pytest.raises(DocumentDownloadError, match="PDF media"):
                await _downloader(client, paths).download("https://arxiv.org/pdf/2607.00001")
        with pytest.raises(DocumentDownloadError, match="approved HTTPS"):
            SafePdfDownloader.validate_url("https://evil.test/paper.pdf")

        signature_transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/pdf"}, content=b"not-a-pdf"
            )
        )
        async with httpx.AsyncClient(transport=signature_transport) as client:
            with pytest.raises(DocumentDownloadError) as captured:
                await _downloader(client, paths).download("https://arxiv.org/pdf/2607.00001")
            assert captured.value.code == "INVALID_PDF_SIGNATURE"
            assert captured.value.quarantined_path is not None

        size_transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Type": "application/pdf", "Content-Length": "999"},
                content=b"%PDF-x",
            )
        )
        async with httpx.AsyncClient(transport=size_transport) as client:
            with pytest.raises(DocumentDownloadError) as captured:
                await _downloader(client, paths, maximum=10).download(
                    "https://arxiv.org/pdf/2607.00001"
                )
            assert captured.value.code == "DOCUMENT_TOO_LARGE"

    asyncio.run(exercise())


def test_parser_preserves_order_pages_empty_pages_and_safe_failures(
    pdf_fixture_root: Path,
) -> None:
    path = pdf_fixture_root / "columns.pdf"
    path.write_bytes(_pdf_bytes(empty_page=True))
    parsed = PdfTextExtractor().extract(path)
    assert parsed.page_count == 2 and parsed.empty_pages == (2,) and parsed.partial
    assert parsed.pages[0].extraction_class is PageExtractionClass.NATIVE_TEXT
    assert parsed.pages[1].extraction_class is PageExtractionClass.EMPTY
    assert [span.page for span in parsed.spans] == [1, 1, 1]
    assert "Left column" in parsed.spans[1].text
    assert "Right column" in parsed.spans[2].text

    malformed = pdf_fixture_root / "malformed.pdf"
    malformed.write_bytes(b"%PDF-invalid")
    with pytest.raises(PdfParseError) as captured:
        PdfTextExtractor().extract(malformed)
    assert captured.value.code == "MALFORMED_PDF"

    encrypted = pymupdf.open()
    encrypted.new_page().insert_text((40, 40), "secret")
    encrypted_path = pdf_fixture_root / "encrypted.pdf"
    encrypted.save(
        encrypted_path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user"
    )
    encrypted.close()
    with pytest.raises(PdfParseError) as captured:
        PdfTextExtractor().extract(encrypted_path)
    assert captured.value.code == "ENCRYPTED_PDF"


def test_ocr_triage_never_ocr_native_text_and_only_ocr_required_image_pages(
    pdf_fixture_root: Path,
) -> None:
    class FakeOcr:
        def __init__(self) -> None:
            self.calls = 0

        def extract_png(self, payload: bytes) -> str:
            assert payload.startswith(b"\x89PNG")
            self.calls += 1
            return "OCR evidence from an image-only page."

    document = pymupdf.open()
    native = document.new_page()
    native.insert_text(
        (40, 40), "This native text is long enough to remain the primary extraction path."
    )
    image_page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(255)
    image_page.insert_image(image_page.rect, stream=pixmap.tobytes("png"))
    suspicious = document.new_page()
    suspicious.insert_text((40, 40), "Short")
    path = pdf_fixture_root / "triage.pdf"
    document.save(path)
    document.close()
    ocr = FakeOcr()

    parsed = PdfTextExtractor(ocr=ocr).extract(path)

    assert ocr.calls == 1
    assert parsed.pages[0].extraction_class is PageExtractionClass.NATIVE_TEXT
    assert parsed.pages[0].extraction_method == "pymupdf"
    assert parsed.pages[1].extraction_class is PageExtractionClass.OCR_REQUIRED
    assert parsed.pages[1].extraction_method == "tesseract"
    assert parsed.pages[2].extraction_class is PageExtractionClass.SUSPICIOUS
    assert parsed.pages[2].extraction_method == "pymupdf"
    assert any(span.metadata.get("extraction_method") == "tesseract" for span in parsed.spans)


def test_processing_persists_immutable_document_evidence_and_ranking(
    document_store: tuple[PathSettings, SQLiteDatabase, sqlite3.Connection],
) -> None:
    paths, _, connection = document_store
    payload = _pdf_bytes()

    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/pdf"}, content=payload
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            service = DocumentProcessingService(
                connection,
                SQLiteRepositories.for_connection(connection),
                paths,
                _downloader(client, paths),
                PdfTextExtractor(),
                clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            )
            first = await service.process(limit=5)
            second = await service.process(limit=5)
        assert first.succeeded == 1 and first.results[0].pages == 1
        assert first.results[0].evidence_spans == 3
        assert second.requested == 0

    asyncio.run(exercise())
    assert connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM evidence_spans").fetchone()[0] == 3
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM evidence_fts WHERE evidence_fts MATCH 'column'"
        ).fetchone()[0]
        == 2
    )
    local_path = str(connection.execute("SELECT local_path FROM documents").fetchone()[0])
    assert not Path(local_path).is_absolute()

    engine = DeterministicRankingEngine(
        connection,
        load_default_taxonomy(),
        clock=lambda: datetime(2026, 7, 20, tzinfo=UTC),
    )
    first_rank = engine.rank_catalog()
    replay = engine.rank_catalog()
    assert first_rank.results_created == 3 and replay.results_created == 3
    assert replay.profile_version == 2 and replay.results_reused == 0
    technical = connection.execute(
        """SELECT total_score,components_json,feature_snapshot_json
        FROM ranking_results rr JOIN ranking_profiles rp ON rp.id=rr.profile_id
        WHERE rr.score_kind='technical' AND rp.active=1"""
    ).fetchone()
    components = __import__("json").loads(technical["components_json"])
    assert float(technical["total_score"]) == pytest.approx(sum(components.values()))
    assert "neutral 0.5" in str(technical["feature_snapshot_json"])


def test_parsing_failure_retains_document_metadata(
    document_store: tuple[PathSettings, SQLiteDatabase, sqlite3.Connection],
) -> None:
    paths, _, connection = document_store

    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-invalid"
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await DocumentProcessingService(
                connection,
                SQLiteRepositories.for_connection(connection),
                paths,
                _downloader(client, paths),
                PdfTextExtractor(),
            ).process(limit=1)
        assert result.failed == 1 and result.results[0].error_code == "MALFORMED_PDF"

    asyncio.run(exercise())
    row = connection.execute("SELECT parse_status,sha256,byte_size FROM documents").fetchone()
    assert tuple(row) == ("failed", hashlib.sha256(b"%PDF-invalid").hexdigest(), 12)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM document_acquisition_attempts WHERE status='failed'"
        ).fetchone()[0]
        == 1
    )


def test_evidence_api_is_paginated_and_never_exposes_local_paths(
    document_store: tuple[PathSettings, SQLiteDatabase, sqlite3.Connection],
) -> None:
    paths, database, connection = document_store
    pdf_path = paths.raw_documents_root / "paper.pdf"
    pdf_path.write_bytes(_pdf_bytes())

    # Use the processing path to create real evidence first.
    async def prepare_and_request() -> None:
        payload = pdf_path.read_bytes()
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/pdf"}, content=payload
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await DocumentProcessingService(
                connection,
                SQLiteRepositories.for_connection(connection),
                paths,
                _downloader(client, paths),
                PdfTextExtractor(),
            ).process(limit=1)
        application = FastAPI()
        application.state.database = database
        application.include_router(evidence_router)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.get("/items/work/evidence", params={"limit": 1, "offset": 0})
            invalid = await client.get("/items/work/evidence", params={"limit": 101})
            missing = await client.get("/items/missing/evidence")
        assert response.status_code == 200 and response.json()["has_more"] is True
        assert "local_path" not in response.text and str(paths.data_root) not in response.text
        assert invalid.status_code == 422 and missing.status_code == 404

    asyncio.run(prepare_and_request())
