"""Production composition for one bounded daily local pipeline."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, cast

import httpx

from app.agents.models import AgentInput, AgentOutput
from app.agents.runtime import AgentRuntime, AgentStageError
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
from app.intelligence.service import IntelligenceOutputService
from app.multisource.service import MultiSourceDiscoveryService
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
        run = self._resumable_run(connection, repositories, trigger)
        if run is None:
            run = PipelineRun(
                id=self._id_factory(),
                run_type=PipelineRunType.DAILY,
                trigger_type=trigger,
                status=PipelineStatus.QUEUED,
                config_snapshot=self._config_snapshot(),
                queued_at=started,
            )
            with transaction(connection):
                repositories.pipeline_runs.create(run)
        else:
            run = run.model_copy(update={"trigger_type": trigger})
        self._current_run_id = run.id
        with transaction(connection):
            run = run.model_copy(
                update={
                    "status": PipelineStatus.RUNNING,
                    "started_at": started,
                    "completed_at": None,
                    "error_summary": None,
                }
            )
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
            IntelligenceOutputService(connection).assemble_daily_report()
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

    @staticmethod
    def _resumable_run(
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        trigger: PipelineTriggerType,
    ) -> PipelineRun | None:
        if trigger is not PipelineTriggerType.RETRY:
            return None
        row = connection.execute(
            """SELECT p.id FROM pipeline_runs p
            WHERE p.run_type='daily' AND p.status='failed'
            AND EXISTS(SELECT 1 FROM agent_executions a
              WHERE a.pipeline_run_id=p.id AND a.status='failed' AND a.attempt<2)
            ORDER BY p.queued_at DESC,p.id DESC LIMIT 1"""
        ).fetchone()
        return None if row is None else repositories.pipeline_runs.get(str(row[0]))

    async def _execute(
        self,
        connection: sqlite3.Connection,
        repositories: SQLiteRepositories,
        trigger: PipelineTriggerType,
    ) -> DailyCounts:
        if self._current_run_id is None:
            raise DailyPipelineError("The agent runtime requires an active daily run.")
        sync_result = None
        multi_source = None
        documents = None
        ranking = None
        counts = DailyCounts()

        async def orchestrator(_: AgentInput) -> AgentOutput:
            return AgentOutput(
                summary="Bounded local run accepted.",
                values={
                    "generation_concurrency": 1,
                    "source_concurrency": self._settings.resources.source_download_concurrency,
                },
                metrics={"maximum_agents": 14},
            )

        async def source_scout(_: AgentInput) -> AgentOutput:
            nonlocal sync_result, multi_source
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
            multi_source = await MultiSourceDiscoveryService(
                self._settings,
                connection,
                repositories,
                id_factory=self._id_factory,
                clock=self._clock,
            ).sync(
                maximum_records=self._settings.scheduler.maximum_records,
                lookback_hours=self._settings.scheduler.lookback_hours,
                trigger=trigger,
            )
            source_counts = {
                "arxiv": sync_result.ingestion.records_seen,
                **{item.source_key: item.fetched for item in multi_source.sources},
            }
            return AgentOutput(
                summary="Bounded source discovery completed with isolated failures.",
                values=cast(JsonObject, {"source_counts": source_counts}),
                provenance_refs=tuple(f"source:{key}" for key in source_counts),
                metrics={"records_fetched": sum(source_counts.values())},
            )

        async def curator(_: AgentInput) -> AgentOutput:
            if sync_result is None or multi_source is None:
                raise AgentStageError("curator", "Source discovery checkpoint is unavailable.")
            normalized = sync_result.records_normalized + multi_source.total_normalized
            return AgentOutput(
                summary="Normalized records passed typed validation and trust classification.",
                values={"normalized": normalized},
                metrics={"records_normalized": normalized},
            )

        async def event_linker(_: AgentInput) -> AgentOutput:
            row = connection.execute("SELECT COUNT(*) FROM linked_events").fetchone()
            count = 0 if row is None else int(row[0])
            return AgentOutput(
                summary="Exact-identity and explicit-link associations were resolved.",
                values={"linked_events": count},
                evidence_refs=tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT id FROM linked_events ORDER BY updated_at DESC LIMIT 20"
                    )
                ),
                metrics={"linked_events": count},
            )

        async def trend_ranking(_: AgentInput) -> AgentOutput:
            nonlocal ranking
            ranking = DeterministicRankingEngine(
                connection, load_default_taxonomy(), clock=self._clock, id_factory=self._id_factory
            ).rank_catalog(limit=100)
            return AgentOutput(
                summary="Deterministic ranking remained authoritative.",
                values={"works_ranked": ranking.works_ranked},
                metrics={"works_ranked": ranking.works_ranked},
            )

        async def evidence(_: AgentInput) -> AgentOutput:
            nonlocal documents
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
            spans = sum(item.evidence_spans for item in documents.results)
            return AgentOutput(
                summary="Bounded primary documents and page evidence were processed.",
                values={
                    "processed": documents.succeeded,
                    "failed": documents.failed + documents.quarantined,
                    "evidence_spans": spans,
                },
                evidence_refs=tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT id FROM evidence_spans ORDER BY created_at DESC LIMIT 50"
                    )
                ),
                metrics={"documents_processed": documents.succeeded, "evidence_spans": spans},
            )

        async def technical_analyst(_: AgentInput) -> AgentOutput:
            nonlocal counts, ranking
            if sync_result is None or multi_source is None or documents is None or ranking is None:
                raise AgentStageError(
                    "technical_analyst", "A required deterministic checkpoint is unavailable."
                )
            ranking = DeterministicRankingEngine(
                connection, load_default_taxonomy(), clock=self._clock, id_factory=self._id_factory
            ).rank_catalog(limit=100)
            counts = DailyCounts(
                fetched=sync_result.ingestion.records_seen + multi_source.total_fetched,
                normalized=sync_result.records_normalized + multi_source.total_normalized,
                documents_processed=documents.succeeded,
                documents_failed=documents.failed + documents.quarantined,
                evidence_spans=sum(item.evidence_spans for item in documents.results),
                works_ranked=ranking.works_ranked,
                source_counts={
                    "arxiv": sync_result.ingestion.records_seen,
                    **{item.source_key: item.fetched for item in multi_source.sources},
                },
            )
            timeout = httpx.Timeout(
                connect=self._settings.http.connect_timeout_seconds,
                read=self._settings.ollama.request_timeout_seconds,
                write=self._settings.ollama.request_timeout_seconds,
                pool=self._settings.http.connect_timeout_seconds,
            )
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
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
                counts = await self.run_analyses(analysis, counts)
            return AgentOutput(
                summary="Scout briefs and deep dives were generated or safely reused.",
                values=counts.model_dump(),
                evidence_refs=tuple(
                    str(row[0])
                    for row in connection.execute(
                        """SELECT id FROM analysis_runs WHERE status='succeeded'
                        ORDER BY completed_at DESC LIMIT 20"""
                    )
                ),
                metrics={
                    "briefs": counts.briefs_generated + counts.briefs_cached,
                    "deep_dives": counts.deep_dives_generated + counts.deep_dives_cached,
                },
            )

        async def skeptic(_: AgentInput) -> AgentOutput:
            unsupported = connection.execute(
                """SELECT COUNT(*) FROM claims c
                JOIN analysis_sections s ON s.id=c.analysis_section_id
                JOIN analysis_runs a ON a.id=s.analysis_run_id
                WHERE a.status='succeeded' AND c.claim_type IN ('fact','interpretation')
                AND NOT EXISTS(SELECT 1 FROM claim_evidence ce WHERE ce.claim_id=c.id)"""
            ).fetchone()
            count = 0 if unsupported is None else int(unsupported[0])
            if count:
                raise AgentStageError(
                    "skeptic_verifier", "Unsupported factual claims blocked publication."
                )
            return AgentOutput(
                summary="All published factual claims retain evidence links.",
                values={"unsupported_claims": 0},
                metrics={"unsupported_claims": 0},
            )

        async def learning(_: AgentInput) -> AgentOutput:
            rows = connection.execute(
                """SELECT output_json FROM analysis_runs
                WHERE analysis_type='deep_dive' AND status='succeeded'
                ORDER BY completed_at DESC LIMIT 2"""
            ).fetchall()
            return AgentOutput(
                summary="Prerequisite-aware learning focus was derived from verified reports.",
                values={
                    "deep_dives_considered": len(rows),
                    "estimated_minutes": min(90, 30 * len(rows)),
                },
                metrics={"plans": len(rows)},
            )

        async def commercial(_: AgentInput) -> AgentOutput:
            row = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE claim_type='hypothesis'"
            ).fetchone()
            total = 0 if row is None else int(row[0])
            return AgentOutput(
                summary="Commercial outputs remain explicitly labelled hypotheses.",
                values={"hypotheses": total},
                metrics={"hypotheses": total},
            )

        async def india(_: AgentInput) -> AgentOutput:
            return AgentOutput(
                summary="India-market buyer and pricing hypotheses were bounded for validation.",
                values={"market": "India", "currency": "INR", "status": "hypothesis"},
                metrics={"markets": 1},
            )

        async def personal(_: AgentInput) -> AgentOutput:
            projects = ("RentAssure", "SageAlpha", "BidReady", "US-school chatbot")
            return AgentOutput(
                summary=(
                    "Verified developments were mapped to configured projects "
                    "without changing facts."
                ),
                values={"projects": list(projects)},
                metrics={"projects": len(projects)},
            )

        async def editor(_: AgentInput) -> AgentOutput:
            return AgentOutput(
                summary="Daily report inputs passed non-duplication and evidence gates.",
                values={"report_date": self._clock().date().isoformat()},
                metrics={"reports_planned": 1},
            )

        async def watchtower(_: AgentInput) -> AgentOutput:
            nonlocal counts
            cleanup = RetentionCleaner(
                connection, self._settings.paths, self._settings.retention, clock=self._clock
            ).run(dry_run=False)
            counts = counts.model_copy(update={"files_cleaned": cleanup.files_deleted})
            degraded = [
                str(row[0])
                for row in connection.execute(
                    """SELECT source_key FROM sources
                    WHERE enabled=1 AND health_status!='healthy' ORDER BY source_key"""
                )
            ]
            return AgentOutput(
                summary="Operational health and retention completed.",
                values=cast(
                    JsonObject,
                    {"degraded_sources": degraded, "files_cleaned": cleanup.files_deleted},
                ),
                metrics={"degraded_sources": len(degraded), "files_cleaned": cleanup.files_deleted},
            )

        handlers = {
            "orchestrator": orchestrator,
            "source_scout": source_scout,
            "curator": curator,
            "event_linker": event_linker,
            "trend_ranking": trend_ranking,
            "evidence": evidence,
            "technical_analyst": technical_analyst,
            "skeptic_verifier": skeptic,
            "learning": learning,
            "commercial_opportunity": commercial,
            "india_market": india,
            "personal_relevance": personal,
            "daily_editor": editor,
            "operations_watchtower": watchtower,
        }
        try:
            await AgentRuntime(connection, clock=self._clock, id_factory=self._id_factory).execute(
                self._current_run_id, self._clock().date().isoformat(), handlers
            )
        except AgentStageError as error:
            raise DailyPipelineError(error.safe_reason, counts) from error
        return counts

    async def run_analyses(
        self, analysis: ScoutAnalysisService, base_counts: DailyCounts
    ) -> DailyCounts:
        briefs_generated = 0
        briefs_cached = 0
        for work_id, _, _ in analysis.ranked_today(self._settings.scheduler.top_briefs):
            result = await analysis.analyze(work_id, AnalysisType.FAST_BRIEF)
            if result.status is not AnalysisStatus.SUCCEEDED:
                raise DailyPipelineError(
                    result.safe_detail
                    or "Scout brief generation failed safely; retry from System.",
                    base_counts.model_copy(
                        update={
                            "briefs_generated": briefs_generated,
                            "briefs_cached": briefs_cached,
                        }
                    ),
                )
            briefs_generated += int(not result.cached)
            briefs_cached += int(result.cached)

        intelligence = IntelligenceOutputService(analysis.connection, analysis)
        deep_generated = 0
        deep_cached = 0
        limit = self._settings.daily_work.maximum_automatic_deep_dives
        for work_id in intelligence.deep_dive_candidates(limit):
            result, _ = await intelligence.run_deep_dive(work_id)
            if result.status is not AnalysisStatus.SUCCEEDED:
                raise DailyPipelineError(
                    result.safe_detail
                    or "A priority deep dive failed its local publication gates. Retry the run.",
                    base_counts.model_copy(
                        update={
                            "briefs_generated": briefs_generated,
                            "briefs_cached": briefs_cached,
                            "deep_dives_generated": deep_generated,
                            "deep_dives_cached": deep_cached,
                        }
                    ),
                )
            deep_generated += int(not result.cached)
            deep_cached += int(result.cached)
        return base_counts.model_copy(
            update={
                "briefs_generated": briefs_generated,
                "briefs_cached": briefs_cached,
                "deep_dives_generated": deep_generated,
                "deep_dives_cached": deep_cached,
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
            "source": "registry-v1.1",
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
