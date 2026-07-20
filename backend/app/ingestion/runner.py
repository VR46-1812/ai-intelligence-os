"""Transactional raw-capture runner for typed source connectors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    JsonObject,
    PipelineRun,
    PipelineRunType,
    PipelineStatus,
    PipelineTriggerType,
    Source,
    SourceHealth,
    SourceRecord,
)
from app.domain.repositories import PipelineRunRepository, SourceRecordRepository, SourceRepository
from app.ingestion.contracts import (
    ConnectorErrorCode,
    ConnectorException,
    ConnectorFailure,
    ConnectorPage,
    CursorCheckpoint,
    FetchWindow,
    SourceConnector,
)
from app.ingestion.registry import SourceRegistry
from app.ingestion.storage import RawPayloadError, RawPayloadStore
from app.repositories.sqlite import RepositoryError

logger = logging.getLogger(__name__)
TransactionFactory = Callable[[], AbstractContextManager[object]]


class IngestionFailureCode(StrEnum):
    CONNECTOR_FAILED = "CONNECTOR_FAILED"
    INVALID_PAGE = "INVALID_PAGE"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class InvalidConnectorPageError(ConnectorException):
    """Raised when a connector violates the typed page/checkpoint contract."""


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    source_key: str
    status: PipelineStatus
    pages_committed: int = Field(ge=0)
    records_seen: int = Field(ge=0)
    records_created: int = Field(ge=0)
    duplicate_records: int = Field(ge=0)
    cursor: JsonObject | None = None
    failure_code: IngestionFailureCode | None = None
    connector_failure: ConnectorFailure | None = None
    safe_message: str | None = None


class IngestionRunner:
    """Capture connector pages with per-page atomic records and checkpoints."""

    def __init__(
        self,
        registry: SourceRegistry,
        sources: SourceRepository,
        records: SourceRecordRepository,
        pipeline_runs: PipelineRunRepository,
        payload_store: RawPayloadStore,
        transaction_factory: TransactionFactory,
        *,
        source_concurrency: int,
        maximum_pages: int = 100,
        id_factory: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 1 <= source_concurrency <= 3:
            raise ValueError("source concurrency must be between 1 and 3")
        if not 1 <= maximum_pages <= 1000:
            raise ValueError("maximum_pages must be between 1 and 1000")
        self._registry = registry
        self._sources = sources
        self._records = records
        self._pipeline_runs = pipeline_runs
        self._payload_store = payload_store
        self._transaction_factory = transaction_factory
        self._source_slots = asyncio.Semaphore(source_concurrency)
        self._maximum_pages = maximum_pages
        self._id_factory = id_factory
        self._clock = clock

    async def run(
        self,
        source_key: str,
        *,
        since: datetime,
        until: datetime,
        page_size: int,
        trigger: PipelineTriggerType = PipelineTriggerType.MANUAL,
    ) -> IngestionResult:
        registered = self._registry.load(source_key)
        source = registered.source
        window = FetchWindow(
            since=since,
            until=until,
            cursor=source.cursor,
            page_size=page_size,
        )
        run = PipelineRun(
            id=self._id_factory(),
            run_type=PipelineRunType.DISCOVER,
            trigger_type=trigger,
            status=PipelineStatus.RUNNING,
            config_snapshot={
                "source_key": source_key,
                "connector_version": registered.connector.connector_version,
                "since": window.since.isoformat(),
                "until": window.until.isoformat(),
                "page_size": window.page_size,
                "maximum_pages": self._maximum_pages,
            },
            queued_at=self._clock(),
            started_at=self._clock(),
        )
        source = source.model_copy(update={"last_attempt_at": self._clock()})
        with self._transaction_factory():
            self._pipeline_runs.create(run)
            self._sources.update(source)

        logger.info("source_ingestion_started", extra={"source_key": source_key, "run_id": run.id})
        async with self._source_slots:
            return await self._consume(registered.connector, source, run, window)

    async def _consume(
        self,
        connector: SourceConnector,
        source: Source,
        run: PipelineRun,
        window: FetchWindow,
    ) -> IngestionResult:
        pages = 0
        seen = 0
        created = 0
        duplicates = 0
        exhausted = False
        try:
            async for page in connector.fetch(window):
                if pages >= self._maximum_pages:
                    raise self._invalid_page("connector exceeded the configured page limit")
                self._validate_page(page, source.source_key, window)
                if (
                    sum(len(record.payload) for record in page.records)
                    > self._payload_store.maximum_bytes
                ):
                    raise RawPayloadError(
                        "connector page exceeded the configured aggregate byte limit"
                    )
                persisted = tuple(
                    (record, self._payload_store.persist(record)) for record in page.records
                )
                now = self._clock()
                next_source = source.model_copy(
                    update={
                        "cursor": page.next_cursor,
                        "last_success_at": now,
                        "health_status": SourceHealth.HEALTHY,
                        "updated_at": now,
                    }
                )
                page_created = 0
                page_duplicates = 0
                with self._transaction_factory():
                    for raw, stored in persisted:
                        outcome = self._records.create_or_get(
                            SourceRecord(
                                id=self._id_factory(),
                                source_id=source.id,
                                upstream_id=raw.upstream_id,
                                upstream_version=raw.upstream_version,
                                canonical_url=raw.canonical_url,
                                payload_sha256=stored.payload_sha256,
                                raw_payload_path=stored.raw_payload_path,
                                observed_at=raw.observed_at,
                                published_at=raw.published_at,
                                updated_at_upstream=raw.updated_at,
                            )
                        )
                        page_created += int(outcome.created)
                        page_duplicates += int(not outcome.created)
                    self._sources.update(next_source)
                source = next_source
                pages += 1
                seen += len(page.records)
                created += page_created
                duplicates += page_duplicates
                exhausted = page.exhausted
                if exhausted:
                    break
            if not exhausted:
                raise self._invalid_page("connector ended without an exhausted page")
        except InvalidConnectorPageError as error:
            return self._finish_failure(
                source,
                run,
                pages_committed=pages,
                records_seen=seen,
                records_created=created,
                duplicate_records=duplicates,
                failure_code=IngestionFailureCode.INVALID_PAGE,
                safe_message=error.failure.safe_message,
                connector_failure=error.failure,
            )
        except ConnectorException as error:
            return self._finish_failure(
                source,
                run,
                pages_committed=pages,
                records_seen=seen,
                records_created=created,
                duplicate_records=duplicates,
                failure_code=IngestionFailureCode.CONNECTOR_FAILED,
                safe_message=error.failure.safe_message,
                connector_failure=error.failure,
            )
        except RawPayloadError as error:
            return self._finish_failure(
                source,
                run,
                pages_committed=pages,
                records_seen=seen,
                records_created=created,
                duplicate_records=duplicates,
                failure_code=IngestionFailureCode.PERSISTENCE_FAILED,
                safe_message=str(error),
            )
        except RepositoryError:
            return self._finish_failure(
                source,
                run,
                pages_committed=pages,
                records_seen=seen,
                records_created=created,
                duplicate_records=duplicates,
                failure_code=IngestionFailureCode.PERSISTENCE_FAILED,
                safe_message="database persistence failed",
            )
        except Exception:
            return self._finish_failure(
                source,
                run,
                pages_committed=pages,
                records_seen=seen,
                records_created=created,
                duplicate_records=duplicates,
                failure_code=IngestionFailureCode.INTERNAL_ERROR,
                safe_message="connector execution failed unexpectedly",
            )

        completed = run.model_copy(
            update={"status": PipelineStatus.SUCCEEDED, "completed_at": self._clock()}
        )
        with self._transaction_factory():
            self._pipeline_runs.update(completed)
        logger.info(
            "source_ingestion_succeeded", extra={"source_key": source.source_key, "run_id": run.id}
        )
        return IngestionResult(
            run_id=run.id,
            source_key=source.source_key,
            status=PipelineStatus.SUCCEEDED,
            pages_committed=pages,
            records_seen=seen,
            records_created=created,
            duplicate_records=duplicates,
            cursor=source.cursor,
        )

    @staticmethod
    def _validate_page(page: ConnectorPage, source_key: str, window: FetchWindow) -> None:
        if len(page.records) > window.page_size:
            raise IngestionRunner._invalid_page("connector page exceeded the requested page size")
        if any(record.source_key != source_key for record in page.records):
            raise IngestionRunner._invalid_page("connector page contained a different source key")
        if not page.exhausted and page.next_cursor is None:
            raise IngestionRunner._invalid_page("non-exhausted connector page omitted its cursor")
        if page.next_cursor is not None:
            try:
                checkpoint = CursorCheckpoint.model_validate(page.next_cursor)
            except ValueError as error:
                raise IngestionRunner._invalid_page(
                    "connector returned an invalid cursor"
                ) from error
            if checkpoint.window_end != window.until:
                raise IngestionRunner._invalid_page(
                    "connector cursor window_end does not match the fetch window"
                )

    @staticmethod
    def _invalid_page(message: str) -> InvalidConnectorPageError:
        return InvalidConnectorPageError(
            ConnectorFailure(
                code=ConnectorErrorCode.INVALID_RESPONSE,
                retryable=False,
                safe_message=message,
                attempts=1,
            )
        )

    def _finish_failure(
        self,
        source: Source,
        run: PipelineRun,
        *,
        pages_committed: int,
        records_seen: int,
        records_created: int,
        duplicate_records: int,
        failure_code: IngestionFailureCode,
        safe_message: str,
        connector_failure: ConnectorFailure | None = None,
    ) -> IngestionResult:
        now = self._clock()
        health = SourceHealth.DEGRADED if pages_committed else SourceHealth.FAILED
        failed_source = source.model_copy(
            update={
                "health_status": health,
                "updated_at": now,
            }
        )
        failed_run = run.model_copy(
            update={
                "status": PipelineStatus.FAILED,
                "completed_at": now,
                "error_summary": f"{failure_code.value}: {safe_message}",
            }
        )
        with self._transaction_factory():
            self._sources.update(failed_source)
            self._pipeline_runs.update(failed_run)
        logger.warning(
            "source_ingestion_failed",
            extra={
                "source_key": source.source_key,
                "run_id": run.id,
                "failure_code": failure_code.value,
            },
        )
        return IngestionResult(
            run_id=run.id,
            source_key=source.source_key,
            status=PipelineStatus.FAILED,
            pages_committed=pages_committed,
            records_seen=records_seen,
            records_created=records_created,
            duplicate_records=duplicate_records,
            cursor=source.cursor,
            failure_code=failure_code,
            connector_failure=connector_failure,
            safe_message=safe_message,
        )
