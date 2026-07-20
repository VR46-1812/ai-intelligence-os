"""Focused deterministic M2.3 discovery service, API, and CLI tests."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import REPOSITORY_ROOT, SourceSettings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.discovery.api import get_discovery_service, router
from app.discovery.cli import main as discovery_main
from app.discovery.models import DiscoverySyncRequest
from app.discovery.service import DiscoveryDisabledError, DiscoveryService
from app.domain.models import (
    PageRequest,
    PipelineRun,
    PipelineRunType,
    PipelineStatus,
    PipelineTriggerType,
    Source,
    SourceHealth,
    TrustTier,
)
from app.ingestion.runner import IngestionResult
from app.repositories import SQLiteRepositories
from app.sources.arxiv_ingestion import ArxivSyncResult
from app.sources.catalog import upsert_arxiv_source

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


class FixtureSyncExecutor:
    def __init__(self) -> None:
        self.requests: list[DiscoverySyncRequest] = []

    async def sync(self, request: DiscoverySyncRequest) -> ArxivSyncResult:
        self.requests.append(request)
        return ArxivSyncResult(
            ingestion=IngestionResult(
                run_id="run-discovery",
                source_key="arxiv",
                status=PipelineStatus.SUCCEEDED,
                pages_committed=1,
                records_seen=request.maximum_records,
                records_created=request.maximum_records,
                duplicate_records=0,
                cursor={"position": str(request.maximum_records)},
            ),
            records_normalized=request.maximum_records,
            records_rejected=0,
            works_created=request.maximum_records,
            revisions_created=0,
            already_known=0,
            manual_review=0,
        )


@pytest.fixture
def discovery_store() -> Iterator[
    tuple[Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor]
]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(root / "discovery.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    repositories = SQLiteRepositories.for_connection(connection)
    executor = FixtureSyncExecutor()
    with transaction(connection):
        repositories.sources.create(
            Source(
                id="source-arxiv",
                source_key="arxiv",
                display_name="arXiv",
                trust_tier=TrustTier.A,
                base_url="https://export.arxiv.org",
                poll_interval_minutes=60,
                minimum_request_interval_ms=3000,
                connector_version="arxiv-v1",
                cursor={"position": "5"},
                health_status=SourceHealth.HEALTHY,
                last_attempt_at=NOW,
                last_success_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        repositories.pipeline_runs.create(
            PipelineRun(
                id="run-discovery",
                run_type=PipelineRunType.DISCOVER,
                trigger_type=PipelineTriggerType.MANUAL,
                status=PipelineStatus.SUCCEEDED,
                config_snapshot={"source_key": "arxiv", "page_size": 5},
                queued_at=NOW,
                started_at=NOW,
                completed_at=NOW,
            )
        )
    service = DiscoveryService(
        repositories.sources,
        repositories.pipeline_runs,
        executor,
    )
    try:
        yield root, connection, repositories, service, executor
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def test_service_lists_sources_reports_health_inspects_run_and_starts_bounded_sync(
    discovery_store: tuple[
        Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor
    ],
) -> None:
    _, _, _, service, executor = discovery_store

    sources = service.list_sources(limit=1, offset=0, enabled=True)
    assert len(sources) == 1
    assert sources[0].source_key == "arxiv"
    health = service.connector_health("arxiv")
    assert health.health_status is SourceHealth.HEALTHY
    assert health.minimum_request_interval_ms == 3000
    assert health.checkpoint == {"position": "5"}
    assert service.inspect_run("run-discovery").status is PipelineStatus.SUCCEEDED

    request = DiscoverySyncRequest(maximum_records=25, lookback_hours=168)
    result = asyncio.run(service.start_sync(request))
    assert result.ingestion.records_seen == 25
    assert executor.requests == [request]
    for maximum_records in (0, 26):
        with pytest.raises(ValidationError):
            DiscoverySyncRequest(maximum_records=maximum_records)
    for lookback_hours in (0, 169):
        with pytest.raises(ValidationError):
            DiscoverySyncRequest(lookback_hours=lookback_hours)


def test_service_refuses_disabled_source(
    discovery_store: tuple[
        Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor
    ],
) -> None:
    _, connection, repositories, service, executor = discovery_store
    source = repositories.sources.get_by_key("arxiv")
    assert source is not None
    with transaction(connection):
        repositories.sources.update(source.model_copy(update={"enabled": False}))

    assert service.connector_health("arxiv").health_status is SourceHealth.DISABLED
    with pytest.raises(DiscoveryDisabledError, match="disabled"):
        asyncio.run(service.start_sync(DiscoverySyncRequest()))
    assert executor.requests == []


async def _exercise_api(service: DiscoveryService) -> None:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_discovery_service] = lambda: service
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        sources = await client.get("/api/discovery/sources", params={"limit": 1})
        health = await client.get("/api/discovery/sources/arxiv/health")
        run = await client.get("/api/discovery/runs/run-discovery")
        sync = await client.post(
            "/api/discovery/sync",
            json={"source_key": "arxiv", "maximum_records": 3, "lookback_hours": 24},
        )
        missing = await client.get("/api/discovery/runs/missing")
        unbounded = await client.post(
            "/api/discovery/sync",
            json={"source_key": "arxiv", "maximum_records": 26, "lookback_hours": 24},
        )

    assert sources.status_code == 200 and sources.json()[0]["source_key"] == "arxiv"
    assert health.status_code == 200 and health.json()["health_status"] == "healthy"
    assert run.status_code == 200 and run.json()["id"] == "run-discovery"
    assert sync.status_code == 200 and sync.json()["ingestion"]["records_seen"] == 3
    assert missing.status_code == 404
    assert unbounded.status_code == 422


def test_api_exposes_all_discovery_operations_with_bounds(
    discovery_store: tuple[
        Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor
    ],
) -> None:
    asyncio.run(_exercise_api(discovery_store[3]))


@pytest.mark.parametrize(
    ("arguments", "expected_key"),
    [
        (("list-sources",), "source_key"),
        (("source-health", "arxiv"), "health_status"),
        (("show-run", "run-discovery"), "run_type"),
        (("sync", "arxiv", "--maximum-records", "2"), "records_normalized"),
    ],
)
def test_cli_exposes_discovery_operations_without_bypassing_service(
    discovery_store: tuple[
        Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor
    ],
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
    expected_key: str,
) -> None:
    service = discovery_store[3]
    discovery_main(arguments, context_factory=lambda: nullcontext(service))

    output = json.loads(capsys.readouterr().out)
    if isinstance(output, list):
        assert expected_key in output[0]
    else:
        assert expected_key in output


def test_source_bootstrap_is_idempotent_and_honors_disabled_configuration(
    discovery_store: tuple[
        Path, sqlite3.Connection, SQLiteRepositories, DiscoveryService, FixtureSyncExecutor
    ],
) -> None:
    _, connection, repositories, _, _ = discovery_store
    with transaction(connection):
        first = upsert_arxiv_source(
            repositories.sources,
            SourceSettings(arxiv_enabled=False, arxiv_categories=("cs.AI",)),
            now=NOW,
        )
        second = upsert_arxiv_source(
            repositories.sources,
            SourceSettings(arxiv_enabled=False, arxiv_categories=("cs.AI",)),
            now=NOW,
        )

    assert first.id == second.id == "source-arxiv"
    assert second.enabled is False
    assert second.health_status is SourceHealth.DISABLED
    assert second.config == {"categories": ["cs.AI"]}
    assert len(repositories.sources.list(PageRequest(limit=10))) == 1
