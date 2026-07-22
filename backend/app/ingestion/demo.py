"""Offline ingestion demo with an explicit bounded live arXiv option."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from app.catalog.identity import CatalogIdentityService, new_ulid, normalize_title
from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import AppSettings, initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.domain.models import PublicationStatus, Source, TrustTier, WorkType
from app.ingestion.contracts import (
    ConnectorPage,
    FetchWindow,
    NormalizedRecord,
    RawSourceRecord,
)
from app.ingestion.http import BoundedHttpClient
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionRunner
from app.ingestion.storage import RawPayloadStore
from app.repositories import SQLiteRepositories
from app.sources.arxiv import ArxivConnector
from app.sources.arxiv_ingestion import ArxivIngestionService, ArxivSyncResult
from app.sources.catalog import upsert_arxiv_source


class LiveDemoError(RuntimeError):
    """Raised when an explicit live demo does not retrieve a useful first page."""


def ensure_useful_live_result(result: ArxivSyncResult) -> None:
    if result.ingestion.records_seen == 0 or not result.fetched_entries:
        raise LiveDemoError(
            "the official arXiv API returned no usable entries on the first bounded page; "
            "no successful live demonstration was produced"
        )


class OfflineFixtureConnector:
    contract_version = "1.0"
    key = "fixture"
    trust_tier = TrustTier.A
    connector_version = "fixture-v1"

    def __init__(self, records: tuple[RawSourceRecord, ...]) -> None:
        self._records = records

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        selected = self._records[: window.page_size]
        yield ConnectorPage(
            records=selected,
            next_cursor={
                "schema_version": 1,
                "position": str(len(selected)),
                "window_end": window.until.isoformat(),
                "last_upstream_id": None if not selected else selected[-1].upstream_id,
            },
            exhausted=True,
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        decoded: object = json.loads(record.payload)
        if not isinstance(decoded, dict):
            raise ValueError("fixture payload is invalid")
        payload = cast(dict[str, object], decoded)
        title = payload.get("title")
        if not isinstance(title, str):
            raise ValueError("fixture payload title is invalid")
        return NormalizedRecord(
            source_key=record.source_key,
            upstream_id=record.upstream_id,
            upstream_version=record.upstream_version,
            work_type=WorkType.PAPER,
            title=title,
            normalized_title=normalize_title(title),
            canonical_url=record.canonical_url,
            publication_status=PublicationStatus.PREPRINT,
            published_at=record.published_at,
            updated_at=record.updated_at,
            identities=(),
            authors=(),
            source_topics=("cs.AI",),
            document_urls=(),
            repository_urls=(),
        )

    def validate(self, record: NormalizedRecord) -> list[str]:
        return [] if record.source_key == self.key else ["source_key does not match connector"]


def _fixture_records(count: int, observed_at: datetime) -> tuple[RawSourceRecord, ...]:
    return tuple(
        RawSourceRecord(
            source_key="fixture",
            upstream_id=f"fixture-{index}",
            upstream_version="v1",
            canonical_url=f"https://example.test/research/fixture-{index}",
            observed_at=observed_at,
            published_at=observed_at - timedelta(days=index),
            media_type="application/json",
            payload=json.dumps({"title": f"Offline research fixture {index}"}).encode(),
            response_metadata={"fixture": True},
        )
        for index in range(1, count + 1)
    )


async def _run(count: int, *, live: bool) -> None:
    settings = load_settings()
    initialize_directories(settings.paths)
    database = SQLiteDatabase(
        settings.paths.database_path,
        settings.database.busy_timeout_ms,
    )
    MigrationRunner(database).migrate()
    connection = database.connect()
    now = datetime.now(UTC)
    try:
        repositories = SQLiteRepositories.for_connection(connection)
        if live:
            await _run_live(count, settings, connection, repositories, now)
            return
        source = repositories.sources.get_by_key("fixture")
        if source is None:
            with transaction(connection):
                repositories.sources.create(
                    Source(
                        id=new_ulid(),
                        source_key="fixture",
                        display_name="Offline M2.1 Fixture",
                        trust_tier=TrustTier.A,
                        base_url="https://example.test",
                        poll_interval_minutes=1440,
                        connector_version="fixture-v1",
                        created_at=now,
                        updated_at=now,
                    )
                )
        connector = OfflineFixtureConnector(_fixture_records(count, now))
        runner = IngestionRunner(
            SourceRegistry(repositories.sources, (connector,)),
            repositories.sources,
            repositories.source_records,
            repositories.pipeline_runs,
            RawPayloadStore(
                settings.paths.data_root,
                settings.paths.raw_documents_root,
                settings.downloads.maximum_document_bytes,
            ),
            lambda: transaction(connection),
            source_concurrency=settings.resources.source_download_concurrency,
            maximum_pages=1,
            id_factory=new_ulid,
        )
        result = await runner.run(
            "fixture",
            since=now - timedelta(days=1),
            until=now,
            page_size=count,
        )
        print(result.model_dump_json(indent=2))
    finally:
        connection.close()


async def _run_live(
    count: int,
    settings: AppSettings,
    connection: sqlite3.Connection,
    repositories: SQLiteRepositories,
    now: datetime,
) -> None:
    if not settings.sources.arxiv_enabled:
        raise RuntimeError("live arXiv retrieval is disabled by AIOS_SOURCES__ARXIV_ENABLED")
    with transaction(connection):
        source = upsert_arxiv_source(
            repositories.sources,
            settings.sources,
            now=now,
        )
        taxonomy = TopicTaxonomyService(repositories.topics, load_default_taxonomy())
        taxonomy.seed()

    http = BoundedHttpClient.from_settings(
        settings.http,
        settings.downloads,
        settings.resources,
    )
    payload_store = RawPayloadStore(
        settings.paths.data_root,
        settings.paths.raw_documents_root,
        settings.downloads.maximum_document_bytes,
    )
    connector = ArxivConnector(
        http,
        settings.sources.arxiv_categories,
        minimum_request_interval_ms=source.minimum_request_interval_ms,
        maximum_pages_per_run=1,
    )
    runner = IngestionRunner(
        SourceRegistry(repositories.sources, (connector,)),
        repositories.sources,
        repositories.source_records,
        repositories.pipeline_runs,
        payload_store,
        lambda: transaction(connection),
        source_concurrency=settings.resources.source_download_concurrency,
        maximum_pages=1,
        id_factory=new_ulid,
    )
    service = ArxivIngestionService(
        runner,
        connector,
        repositories.sources,
        repositories.source_records,
        CatalogIdentityService(
            repositories.works,
            repositories.work_versions,
            repositories.catalog_identities,
        ),
        repositories.catalog_identities,
        taxonomy,
        repositories.topics,
        payload_store,
        lambda: transaction(connection),
        clock=lambda: datetime.now(UTC),
    )
    try:
        result = await service.sync(
            since=now - timedelta(days=7),
            until=now,
            page_size=count,
        )
        ensure_useful_live_result(result)
        print(result.model_dump_json(indent=2))
    finally:
        await http.aclose()


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, choices=range(1, 6), default=5)
    parser.add_argument(
        "--live",
        action="store_true",
        help="fetch and store at most five real records from the official arXiv API",
    )
    options = parser.parse_args(arguments)
    try:
        asyncio.run(_run(options.records, live=options.live))
    except LiveDemoError as error:
        parser.exit(2, f"live arXiv demo failed: {error}\n")


if __name__ == "__main__":
    main()
