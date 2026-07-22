"""Fixture-driven M2.1 registry, provenance, pagination, and recovery tests."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import REPOSITORY_ROOT
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import MigrationRunner
from app.domain.models import (
    JsonObject,
    PageRequest,
    PipelineStatus,
    Source,
    SourceHealth,
    TrustTier,
)
from app.ingestion.contracts import (
    ConnectorErrorCode,
    ConnectorException,
    ConnectorFailure,
    ConnectorPage,
    FetchWindow,
    NormalizedRecord,
    RawSourceRecord,
)
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionFailureCode, IngestionRunner
from app.ingestion.storage import RawPayloadStore
from app.repositories.sqlite import SQLiteRepositories

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
ID_SEQUENCE = count(1)


class FixtureConnector:
    contract_version = "1.0"
    key = "fixture"
    trust_tier = TrustTier.A
    connector_version = "fixture-v1"

    def __init__(
        self,
        records: tuple[RawSourceRecord, ...],
        *,
        fail_after_pages: int | None = None,
        invalid_cursor: bool = False,
    ) -> None:
        self.records = records
        self.fail_after_pages = fail_after_pages
        self.invalid_cursor = invalid_cursor
        self.windows: list[FetchWindow] = []

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        self.windows.append(window)
        start = 0 if window.cursor is None else int(str(window.cursor["position"]))
        pages_yielded = 0
        while start < len(self.records):
            end = min(len(self.records), start + window.page_size)
            exhausted = end == len(self.records)
            cursor: JsonObject = (
                {"position": str(end)}
                if self.invalid_cursor
                else {
                    "schema_version": 1,
                    "position": str(end),
                    "window_end": window.until.isoformat(),
                    "last_upstream_id": self.records[end - 1].upstream_id,
                }
            )
            yield ConnectorPage(
                records=self.records[start:end],
                next_cursor=cursor,
                exhausted=exhausted,
            )
            pages_yielded += 1
            if self.fail_after_pages is not None and pages_yielded >= self.fail_after_pages:
                raise ConnectorException(
                    ConnectorFailure(
                        code=ConnectorErrorCode.UPSTREAM_5XX,
                        retryable=True,
                        safe_message="fixture source failed after a committed page",
                        attempts=4,
                        status_code=503,
                    )
                )
            start = end

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        raise NotImplementedError

    def validate(self, record: NormalizedRecord) -> list[str]:
        return []


@pytest.fixture
def ingestion_store() -> Iterator[tuple[Path, sqlite3.Connection, SQLiteRepositories]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(path=root / "ingestion.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    repositories = SQLiteRepositories.for_connection(connection)
    with transaction(connection):
        repositories.sources.create(
            Source(
                id="source-fixture",
                source_key="fixture",
                display_name="Fixture Source",
                trust_tier=TrustTier.A,
                base_url="https://source.test",
                poll_interval_minutes=60,
                minimum_request_interval_ms=100,
                connector_version="fixture-v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    try:
        yield root, connection, repositories
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def _records(count: int = 3) -> tuple[RawSourceRecord, ...]:
    return tuple(
        RawSourceRecord(
            source_key="fixture",
            upstream_id=f"record-{index}",
            upstream_version="v1",
            canonical_url=f"https://source.test/record-{index}",
            observed_at=NOW + timedelta(minutes=index),
            published_at=NOW - timedelta(days=index),
            media_type="application/json",
            payload=json.dumps({"id": index, "title": f"Fixture {index}"}).encode(),
            response_metadata={"etag": f"etag-{index}", "status_code": 200},
        )
        for index in range(1, count + 1)
    )


def _id_factory() -> Callable[[], str]:
    return lambda: f"ingestion-id-{next(ID_SEQUENCE)}"


def _runner(
    root: Path,
    connection: sqlite3.Connection,
    repositories: SQLiteRepositories,
    connector: FixtureConnector,
    *,
    maximum_bytes: int = 10_000,
) -> IngestionRunner:
    return IngestionRunner(
        SourceRegistry(repositories.sources, (connector,)),
        repositories.sources,
        repositories.source_records,
        repositories.pipeline_runs,
        RawPayloadStore(root, root / "raw", maximum_bytes),
        lambda: transaction(connection),
        source_concurrency=3,
        maximum_pages=10,
        id_factory=_id_factory(),
        clock=lambda: NOW,
    )


def _run(runner: IngestionRunner, page_size: int = 2):
    return asyncio.run(
        runner.run(
            "fixture",
            since=NOW - timedelta(days=1),
            until=NOW,
            page_size=page_size,
        )
    )


def test_fixture_connector_paginates_and_persists_raw_provenance(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = ingestion_store
    connector = FixtureConnector(_records())
    result = _run(_runner(root, connection, repositories, connector))

    assert result.status is PipelineStatus.SUCCEEDED
    assert (result.pages_committed, result.records_seen, result.records_created) == (2, 3, 3)
    assert result.cursor is not None and result.cursor["position"] == "3"
    persisted = repositories.source_records.list(PageRequest())
    assert len(persisted) == 3
    for record in persisted:
        payload_path = root / record.raw_payload_path
        metadata_paths = tuple(payload_path.parent.glob(f"{record.payload_sha256}.*.metadata.json"))
        assert payload_path.is_file() and len(metadata_paths) == 1
        metadata_path = metadata_paths[0]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["payload_sha256"] == record.payload_sha256
        assert metadata["response_metadata"]["status_code"] == 200
    run = repositories.pipeline_runs.get(result.run_id)
    assert run is not None and run.status is PipelineStatus.SUCCEEDED


def test_reingestion_is_idempotent_for_same_upstream_payload(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = ingestion_store
    records = _records(2)
    first = _run(_runner(root, connection, repositories, FixtureConnector(records)))
    source = repositories.sources.get_by_key("fixture")
    assert source is not None
    with transaction(connection):
        repositories.sources.update(source.model_copy(update={"cursor": None}))
    repeated_records = tuple(
        record.model_copy(update={"response_metadata": {"etag": "observed-again"}})
        for record in records
    )
    repeated = _run(_runner(root, connection, repositories, FixtureConnector(repeated_records)))

    assert first.records_created == 2
    assert repeated.records_created == 0
    assert repeated.duplicate_records == 2
    assert len(repositories.source_records.list(PageRequest())) == 2
    persisted = repositories.source_records.list(PageRequest())[0]
    payload_path = root / persisted.raw_payload_path
    assert len(tuple(payload_path.parent.glob(f"{persisted.payload_sha256}.*.metadata.json"))) == 2


def test_failure_after_page_preserves_checkpoint_and_recovery_resumes(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = ingestion_store
    records = _records(3)
    failed_connector = FixtureConnector(records, fail_after_pages=1)
    failed = _run(_runner(root, connection, repositories, failed_connector))

    assert failed.status is PipelineStatus.FAILED
    assert failed.failure_code is IngestionFailureCode.CONNECTOR_FAILED
    assert failed.pages_committed == 1
    assert failed.records_created == 2
    source = repositories.sources.get_by_key("fixture")
    assert source is not None and source.cursor is not None
    assert source.cursor["position"] == "2"
    assert source.health_status is SourceHealth.DEGRADED

    recovery_connector = FixtureConnector(records)
    recovered = _run(_runner(root, connection, repositories, recovery_connector))
    assert recovered.status is PipelineStatus.SUCCEEDED
    assert recovered.records_created == 1
    assert recovery_connector.windows[0].cursor is not None
    assert recovery_connector.windows[0].cursor["position"] == "2"
    assert len(repositories.source_records.list(PageRequest())) == 3


def test_payload_failure_rolls_back_records_and_does_not_advance_cursor(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = ingestion_store
    result = _run(
        _runner(root, connection, repositories, FixtureConnector(_records(2)), maximum_bytes=1)
    )

    assert result.status is PipelineStatus.FAILED
    assert result.failure_code is IngestionFailureCode.PERSISTENCE_FAILED
    assert result.pages_committed == 0
    assert repositories.source_records.list(PageRequest()) == ()
    source = repositories.sources.get_by_key("fixture")
    assert source is not None and source.cursor is None


def test_invalid_cursor_is_rejected_before_page_capture(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = ingestion_store
    result = _run(
        _runner(root, connection, repositories, FixtureConnector(_records(1), invalid_cursor=True))
    )

    assert result.status is PipelineStatus.FAILED
    assert result.failure_code is IngestionFailureCode.INVALID_PAGE
    assert result.connector_failure is not None
    assert result.connector_failure.code is ConnectorErrorCode.INVALID_RESPONSE
    assert repositories.source_records.list(PageRequest()) == ()


def test_registry_rejects_disabled_missing_and_version_mismatched_sources(
    ingestion_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    _, connection, repositories = ingestion_store
    connector = FixtureConnector(_records(1))
    registry = SourceRegistry(repositories.sources, (connector,))
    assert registry.load("fixture").connector is connector
    with pytest.raises(ConnectorException, match="not registered"):
        registry.load("missing")

    source = repositories.sources.get_by_key("fixture")
    assert source is not None
    with transaction(connection):
        repositories.sources.update(source.model_copy(update={"enabled": False}))
    with pytest.raises(ConnectorException, match="disabled"):
        registry.load("fixture")
    with transaction(connection):
        repositories.sources.update(
            source.model_copy(update={"connector_version": "unexpected-version"})
        )
    with pytest.raises(ConnectorException, match="version"):
        registry.load("fixture")


def test_fetch_window_rejects_reversed_dates_and_unbounded_page_sizes() -> None:
    with pytest.raises(ValidationError, match="since cannot be after until"):
        FetchWindow(since=NOW, until=NOW - timedelta(seconds=1), page_size=5)
    for page_size in (0, 101):
        with pytest.raises(ValidationError):
            FetchWindow(since=NOW - timedelta(days=1), until=NOW, page_size=page_size)
