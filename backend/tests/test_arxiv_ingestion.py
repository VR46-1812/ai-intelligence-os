"""Vertical M2.2 arXiv raw-to-catalog persistence tests."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from uuid import uuid4

import pytest

from app.catalog.identity import CatalogIdentityService
from app.catalog.taxonomy import TopicTaxonomyService, load_default_taxonomy
from app.config import REPOSITORY_ROOT
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import MigrationRunner
from app.domain.models import (
    NormalizationStatus,
    PageRequest,
    Source,
    SourceRecordFilter,
    TrustTier,
    WorkVersionFilter,
)
from app.ingestion.contracts import HttpResponse
from app.ingestion.registry import SourceRegistry
from app.ingestion.runner import IngestionRunner
from app.ingestion.storage import RawPayloadStore
from app.repositories.sqlite import SQLiteRepositories
from app.sources.arxiv import ArxivConnector
from app.sources.arxiv_ingestion import ArxivIngestionService

FIXTURE = Path(__file__).parent / "fixtures" / "arxiv" / "page.xml"
NOW = datetime(2026, 7, 21, tzinfo=UTC)
SINCE = datetime(2026, 7, 17, tzinfo=UTC)


class FixtureHttpClient:
    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: tuple[str, ...],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        del source_key, minimum_request_interval_ms, expected_media_types, headers
        return HttpResponse(
            status_code=200,
            media_type="application/atom+xml",
            content=FIXTURE.read_bytes(),
            response_metadata={"request_url": url, "fixture": True},
        )


@pytest.fixture
def arxiv_store() -> Iterator[tuple[Path, sqlite3.Connection, SQLiteRepositories]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(root / "arxiv.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    repositories = SQLiteRepositories.for_connection(connection)
    taxonomy = TopicTaxonomyService(repositories.topics, load_default_taxonomy())
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
                config={"categories": ["cs.AI", "cs.LG", "cs.CL"]},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        taxonomy.seed()
    try:
        yield root, connection, repositories
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def _ids() -> Callable[[], str]:
    values = count(1)
    return lambda: f"arxiv-id-{next(values)}"


def _service(
    root: Path,
    connection: sqlite3.Connection,
    repositories: SQLiteRepositories,
) -> ArxivIngestionService:
    id_factory = _ids()
    connector = ArxivConnector(
        FixtureHttpClient(),
        ("cs.AI", "cs.LG", "cs.CL"),
        maximum_pages_per_run=1,
        clock=lambda: NOW,
    )
    payload_store = RawPayloadStore(root, root / "raw", 1_000_000)
    runner = IngestionRunner(
        SourceRegistry(repositories.sources, (connector,)),
        repositories.sources,
        repositories.source_records,
        repositories.pipeline_runs,
        payload_store,
        lambda: transaction(connection),
        source_concurrency=3,
        maximum_pages=1,
        id_factory=id_factory,
        clock=lambda: NOW,
    )
    taxonomy = TopicTaxonomyService(repositories.topics, load_default_taxonomy())
    catalog = CatalogIdentityService(
        repositories.works,
        repositories.work_versions,
        repositories.catalog_identities,
        id_factory=id_factory,
        clock=lambda: NOW,
    )
    return ArxivIngestionService(
        runner,
        connector,
        repositories.sources,
        repositories.source_records,
        catalog,
        repositories.catalog_identities,
        taxonomy,
        repositories.topics,
        payload_store,
        lambda: transaction(connection),
        id_factory=id_factory,
        clock=lambda: NOW,
    )


def test_sync_persists_raw_works_revisions_authors_topics_and_isolates_bad_entries(
    arxiv_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = arxiv_store
    result = asyncio.run(
        _service(root, connection, repositories).sync(since=SINCE, until=NOW, page_size=5)
    )

    assert result.ingestion.records_created == 4
    assert result.records_normalized == 3
    assert result.records_rejected == 1
    assert result.works_created == 2
    assert result.revisions_created == 1
    works = repositories.works.list(PageRequest(limit=10))
    assert len(works) == 2
    revised = next(work for work in works if work.normalized_title.startswith("reliable local"))
    versions = repositories.work_versions.list(
        PageRequest(limit=10), WorkVersionFilter(work_id=revised.id)
    )
    assert {version.version_label for version in versions} == {"v1", "v2"}
    assert sum(version.is_current for version in versions) == 1
    assert next(version for version in versions if version.is_current).version_label == "v2"

    author_rows = connection.execute(
        "SELECT a.display_name, wa.author_order FROM authors a "
        "JOIN work_authors wa ON wa.author_id=a.id WHERE wa.work_id=? ORDER BY wa.author_order",
        (revised.id,),
    ).fetchall()
    assert [tuple(row) for row in author_rows] == [
        ("Ada Lovelace", 1),
        ("Grace Hopper", 2),
    ]
    topic_keys = {
        row[0]
        for row in connection.execute(
            "SELECT t.topic_key FROM work_topics wt JOIN topics t ON t.id=wt.topic_id "
            "WHERE wt.work_id=?",
            (revised.id,),
        ).fetchall()
    }
    assert topic_keys == {"agentic-systems", "ai-quality-security", "local-models"}
    failed = repositories.source_records.list(
        PageRequest(limit=10),
        SourceRecordFilter(normalization_status=NormalizationStatus.FAILED),
    )
    assert len(failed) == 1 and failed[0].error_code == "NORMALIZATION_FAILED"


def test_overlapping_repeat_deduplicates_raw_records_works_and_versions(
    arxiv_store: tuple[Path, sqlite3.Connection, SQLiteRepositories],
) -> None:
    root, connection, repositories = arxiv_store
    service = _service(root, connection, repositories)
    first = asyncio.run(service.sync(since=SINCE, until=NOW, page_size=5))
    source = repositories.sources.get_by_key("arxiv")
    assert source is not None
    with transaction(connection):
        repositories.sources.update(source.model_copy(update={"cursor": None}))

    repeated = asyncio.run(service.sync(since=SINCE, until=NOW, page_size=5))

    assert first.ingestion.records_created == 4
    assert repeated.ingestion.records_created == 0
    assert repeated.ingestion.duplicate_records == 4
    assert repeated.records_normalized == 0
    assert len(repositories.source_records.list(PageRequest(limit=10))) == 4
    works = repositories.works.list(PageRequest(limit=10))
    assert len(works) == 2
    assert (
        sum(
            len(
                repositories.work_versions.list(
                    PageRequest(limit=10), WorkVersionFilter(work_id=work.id)
                )
            )
            for work in works
        )
        == 3
    )
