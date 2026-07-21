"""Production composition for one bounded daily local pipeline."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.analysis.ollama import OllamaClient
from app.analysis.service import ScoutAnalysisService
from app.catalog.identity import new_ulid
from app.catalog.taxonomy import load_default_taxonomy
from app.config import AppSettings
from app.db import SQLiteDatabase, transaction
from app.discovery.arxiv import ArxivDiscoverySyncExecutor
from app.discovery.models import DiscoverySyncRequest
from app.documents.download import SafePdfDownloader
from app.documents.ocr import TesseractAdapter
from app.documents.parser import PdfTextExtractor
from app.documents.service import DocumentProcessingService
from app.domain.models import (
    AnalysisStatus,
    AnalysisType,
    JsonObject,
    PageRequest,
    PipelineRun,
    PipelineRunFilter,
    PipelineRunType,
    PipelineStatus,
    PipelineTriggerType,
)
from app.operations.cleanup import RetentionCleaner
from app.operations.models import CleanupResult, DailyCounts, DailyRunResult, DailyRunStatus
from app.ranking.engine import DeterministicRankingEngine
from app.repositories import SQLiteRepositories

logger = logging.getLogger(__name__)


class DailyRunBusyError(RuntimeError):
    """Raised when a second daily run is requested."""


class DailyPipelineError(RuntimeError):
    def __init__(self, safe_detail: str, counts: DailyCounts | None = None) -> None:
        super().__init__(safe_detail)
        self.safe_detail = safe_detail
        self.counts = counts if counts is not None else DailyCounts()


class DailyRunner(Protocol):
    async def run(self, trigger: PipelineTriggerType) -> DailyRunResult: ...
    def status(self) -> DailyRunStatus: ...
    def recover_stale_runs(self) -> int: ...


class ProductionDailyRunner:
    """Compose existing bounded services and persist a public-safe daily summary."""

    def __init__(
        self,
        settings: AppSettings,
        database: SQLiteDatabase,
        run_lock: asyncio.Lock,
        discovery_lock: asyncio.Lock,
        generation_semaphore: asyncio.Semaphore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = new_ulid,
        execute_steps: Callable[
            [sqlite3.Connection, SQLiteRepositories, PipelineTriggerType], Awaitable[DailyCounts]
        ]
        | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._run_lock = run_lock
        self._discovery_lock = discovery_lock
        self._generation_semaphore = generation_semaphore
        self._clock = clock
        self._id_factory = id_factory
        self._execute_steps = execute_steps if execute_steps is not None else self._execute
        self._current_run_id: str | None = None

    async def run(self, trigger: PipelineTriggerType) -> DailyRunResult:
        if self._run_lock.locked():
            raise DailyRunBusyError("A local daily pipeline is already running.")
        async with self._run_lock:
            connection = self._database.connect()
            try:
                return await self._run_connected(connection, trigger)
            finally:
                self._current_run_id = None
                connection.close()

    async def _run_connected(
        self, connection: sqlite3.Connection, trigger: PipelineTriggerType
    ) -> DailyRunResult:
        repositories = SQLiteRepositories.for_connection(connection)
        started = self._clock().astimezone(UTC)
        run = PipelineRun(
            id=self._id_factory(),
            run_type=PipelineRunType.DAILY,
            trigger_type=trigger,
            status=PipelineStatus.QUEUED,
            config_snapshot=self._config_snapshot(),
            queued_at=started,
        )
        self._current_run_id = run.id
        with transaction(connection):
            repositories.pipeline_runs.create(run)
            run = run.model_copy(update={"status": PipelineStatus.RUNNING, "started_at": started})
            repositories.pipeline_runs.update(run)
        counts = DailyCounts()
        try:
            counts = await self._execute_steps(connection, repositories, trigger)
            completed = self._clock().astimezone(UTC)
            run = run.model_copy(
                update={
                    "status": PipelineStatus.SUCCEEDED,
                    "completed_at": completed,
                    "config_snapshot": {
                        **self._config_snapshot(),
                        "result": counts.model_dump(),
                    },
                }
            )
            with transaction(connection):
                repositories.pipeline_runs.update(run)
            return DailyRunResult(
                run_id=run.id,
                status=run.status,
                trigger=trigger,
                counts=counts,
                started_at=started,
                completed_at=completed,
            )
        except DailyPipelineError as error:
            detail = error.safe_detail
            counts = error.counts
        except Exception:
            logger.exception("daily_pipeline_failed", extra={"run_id": run.id})
            detail = "The local daily pipeline stopped safely. Review System and retry."
        completed = self._clock().astimezone(UTC)
        failed = run.model_copy(
            update={
                "status": PipelineStatus.FAILED,
                "completed_at": completed,
                "error_summary": detail,
                "config_snapshot": {**self._config_snapshot(), "result": counts.model_dump()},
            }
        )
        with transaction(connection):
            repositories.pipeline_runs.update(failed)
        return DailyRunResult(
            run_id=run.id,
            status=PipelineStatus.FAILED,
            trigger=trigger,
            counts=counts,
            started_at=started,
            completed_at=completed,
            safe_detail=detail,
        )

    async def _execute(
        self,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        trigger: PipelineTriggerType,
    ) -> DailyCounts:
        sync_result = await ArxivDiscoverySyncExecutor(
            self._settings,
            connection,
            repositories,
            clock=self._clock,
            sync_lock=self._discovery_lock,
        ).sync(
            DiscoverySyncRequest(
                maximum_records=self._settings.scheduler.maximum_records,
                lookback_hours=self._settings.scheduler.lookback_hours,
            ),
            trigger=trigger,
        )
        timeout = httpx.Timeout(
            connect=self._settings.http.connect_timeout_seconds,
            read=self._settings.http.read_timeout_seconds,
            write=self._settings.http.read_timeout_seconds,
            pool=self._settings.http.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": self._settings.http.user_agent},
            limits=httpx.Limits(
                max_connections=self._settings.resources.source_download_concurrency
            ),
        ) as document_client:
            downloader = SafePdfDownloader(
                document_client,
                destination=self._settings.paths.raw_documents_root / "pdf",
                temporary=self._settings.paths.temporary_root,
                quarantine=self._settings.paths.quarantine_root,
                maximum_bytes=self._settings.downloads.maximum_document_bytes,
                chunk_bytes=self._settings.downloads.chunk_bytes,
                concurrency=self._settings.resources.source_download_concurrency,
                maximum_retries=self._settings.http.maximum_retries,
            )
            ocr = (
                TesseractAdapter(
                    executable=self._settings.ocr.tesseract_executable,
                    temporary_root=self._settings.paths.temporary_root,
                    language=self._settings.ocr.language,
                    timeout_seconds=self._settings.ocr.page_timeout_seconds,
                )
                if self._settings.ocr.enabled
                else None
            )
            documents = await DocumentProcessingService(
                connection,
                repositories,
                self._settings.paths,
                downloader,
                PdfTextExtractor(
                    suspicious_native_characters=self._settings.ocr.suspicious_native_characters,
                    ocr=ocr,
                ),
                clock=self._clock,
                id_factory=self._id_factory,
            ).process(limit=self._settings.scheduler.document_limit)
        ranking = DeterministicRankingEngine(
            connection,
            load_default_taxonomy(),
            clock=self._clock,
            id_factory=self._id_factory,
        ).rank_catalog(limit=100)
        generated = 0
        cached = 0
        base_counts = DailyCounts(
            fetched=sync_result.ingestion.records_seen,
            normalized=sync_result.records_normalized,
            documents_processed=documents.succeeded,
            documents_failed=documents.failed + documents.quarantined,
            evidence_spans=sum(item.evidence_spans for item in documents.results),
            works_ranked=ranking.works_ranked,
        )
        analysis_timeout = httpx.Timeout(
            connect=self._settings.http.connect_timeout_seconds,
            read=self._settings.ollama.request_timeout_seconds,
            write=self._settings.ollama.request_timeout_seconds,
            pool=self._settings.http.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=analysis_timeout, follow_redirects=False) as client:
            analysis = ScoutAnalysisService(
                connection,
                repositories,
                OllamaClient(
                    client,
                    base_url=str(self._settings.ollama.base_url),
                    generation_semaphore=self._generation_semaphore,
                    resources=self._settings.resources,
                ),
                self._settings,
                clock=self._clock,
            )
            for work_id, _, _ in analysis.ranked_today(self._settings.scheduler.top_briefs):
                result = await analysis.analyze(work_id, AnalysisType.FAST_BRIEF)
                if result.status is not AnalysisStatus.SUCCEEDED:
                    raise DailyPipelineError(
                        result.safe_detail
                        or "Scout brief generation failed safely; retry from System.",
                        base_counts.model_copy(
                            update={"briefs_generated": generated, "briefs_cached": cached}
                        ),
                    )
                generated += int(not result.cached)
                cached += int(result.cached)
        cleanup = RetentionCleaner(
            connection, self._settings.paths, self._settings.retention, clock=self._clock
        ).run(dry_run=False)
        return base_counts.model_copy(
            update={
                "briefs_generated": generated,
                "briefs_cached": cached,
                "files_cleaned": cleanup.files_deleted,
            }
        )

    def status(self) -> DailyRunStatus:
        connection = self._database.connect()
        try:
            repositories = SQLiteRepositories.for_connection(connection)
            rows = repositories.pipeline_runs.list(
                PageRequest(limit=20), PipelineRunFilter(run_type=PipelineRunType.DAILY)
            )
            latest = None if not rows else self._result_from_run(rows[0])
            latest_success_row = connection.execute(
                """SELECT completed_at FROM pipeline_runs
                WHERE run_type='daily' AND status='succeeded'
                ORDER BY completed_at DESC,id DESC LIMIT 1"""
            ).fetchone()
            latest_success = (
                None if latest_success_row is None else latest_success_row["completed_at"]
            )
            return DailyRunStatus(
                scheduler_enabled=self._settings.scheduler.enabled,
                schedule=(
                    f"{self._settings.scheduler.hour:02d}:{self._settings.scheduler.minute:02d} "
                    f"{self._settings.scheduler.timezone}"
                ),
                running=self._run_lock.locked(),
                current_run_id=self._current_run_id if self._run_lock.locked() else None,
                latest_run=latest,
                latest_success_at=latest_success,
            )
        finally:
            connection.close()

    def recover_stale_runs(self) -> int:
        connection = self._database.connect()
        try:
            repositories = SQLiteRepositories.for_connection(connection)
            rows = [
                *repositories.pipeline_runs.list(
                    PageRequest(limit=100), PipelineRunFilter(status=PipelineStatus.RUNNING)
                ),
                *repositories.pipeline_runs.list(
                    PageRequest(limit=100), PipelineRunFilter(status=PipelineStatus.QUEUED)
                ),
            ]
            recovered = 0
            with transaction(connection):
                for row in rows:
                    if row.run_type is not PipelineRunType.DAILY:
                        continue
                    repositories.pipeline_runs.update(
                        row.model_copy(
                            update={
                                "status": PipelineStatus.FAILED,
                                "completed_at": self._clock().astimezone(UTC),
                                "error_summary": (
                                    "The previous local process stopped before this run completed. "
                                    "It is safe to retry."
                                ),
                            }
                        )
                    )
                    recovered += 1
            return recovered
        finally:
            connection.close()

    def cleanup(self, *, dry_run: bool) -> CleanupResult:
        connection = self._database.connect()
        try:
            return RetentionCleaner(
                connection, self._settings.paths, self._settings.retention, clock=self._clock
            ).run(dry_run=dry_run)
        finally:
            connection.close()

    def _config_snapshot(self) -> JsonObject:
        return {
            "source": "arxiv",
            "maximum_records": self._settings.scheduler.maximum_records,
            "lookback_hours": self._settings.scheduler.lookback_hours,
            "document_limit": self._settings.scheduler.document_limit,
            "top_briefs": self._settings.scheduler.top_briefs,
            "model": self._settings.models.scout.model,
        }

    @staticmethod
    def _result_from_run(run: PipelineRun) -> DailyRunResult:
        raw_counts = run.config_snapshot.get("result", {})
        counts = DailyCounts.model_validate(raw_counts if isinstance(raw_counts, dict) else {})
        return DailyRunResult(
            run_id=run.id,
            status=run.status,
            trigger=run.trigger_type,
            counts=counts,
            started_at=run.started_at or run.queued_at,
            completed_at=run.completed_at,
            safe_detail=run.error_summary,
        )
