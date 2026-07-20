"""Offline bounded demonstration of the M2.1 ingestion framework."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from app.catalog.identity import new_ulid, normalize_title
from app.config import initialize_directories, load_settings
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.domain.models import PublicationStatus, Source, TrustTier, WorkType
from app.ingestion.contracts import (
    ConnectorPage,
    FetchWindow,
    NormalizedRecord,
    RawSourceRecord,
)
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionRunner
from app.ingestion.storage import RawPayloadStore
from app.repositories import SQLiteRepositories


class OfflineFixtureConnector:
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


async def _run(count: int) -> None:
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


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, choices=range(1, 6), default=5)
    options = parser.parse_args(arguments)
    asyncio.run(_run(options.records))


if __name__ == "__main__":
    main()
