"""Deterministic catalog query and public API tests for the Explore slice."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.catalog.api import get_catalog_repository, router
from app.catalog.query import SQLiteCatalogReadRepository
from app.catalog.read_models import (
    CatalogFilterOptions,
    CatalogPaper,
    CatalogPaperPage,
    CatalogPaperQuery,
    CatalogSort,
)
from app.config import REPOSITORY_ROOT
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.repositories.sqlite import RepositoryError


class FailingCatalogRepository:
    def list_papers(self, query: CatalogPaperQuery) -> CatalogPaperPage:
        del query
        raise RepositoryError("SELECT raw_payload_path FROM private_table")

    def get_paper(self, paper_id: str) -> CatalogPaper | None:
        del paper_id
        raise RepositoryError("internal database detail")

    def filter_options(self) -> CatalogFilterOptions:
        raise RepositoryError("internal filter query")


@pytest.fixture
def catalog_store() -> Iterator[tuple[Path, sqlite3.Connection, SQLiteCatalogReadRepository]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(root / "catalog.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    with transaction(connection):
        connection.execute(
            """INSERT INTO sources(
            id, source_key, display_name, trust_tier, base_url, poll_interval_minutes,
            connector_version, created_at, updated_at
            ) VALUES ('source-arxiv', 'arxiv', 'arXiv', 'A', 'https://export.arxiv.org',
            60, 'arxiv-v1', '2026-07-20T00:00:00Z', '2026-07-20T00:00:00Z')"""
        )
        connection.executemany(
            """INSERT INTO source_records(
            id, source_id, upstream_id, upstream_version, canonical_url, payload_sha256,
            raw_payload_path, observed_at, published_at, normalization_status
            ) VALUES (?, 'source-arxiv', ?, 'v1', ?, ?, ?, ?, ?, 'normalized')""",
            [
                (
                    "record-agent",
                    "2607.00001",
                    "https://arxiv.org/abs/2607.00001",
                    "hash-agent",
                    "raw/private-agent.xml",
                    "2026-07-17T12:00:00Z",
                    "2026-07-17T10:00:00Z",
                ),
                (
                    "record-local",
                    "2607.00002",
                    "https://arxiv.org/abs/2607.00002",
                    "hash-local",
                    "raw/private-local.xml",
                    "2026-07-18T12:00:00Z",
                    "2026-07-18T10:00:00Z",
                ),
                (
                    "record-vision",
                    "2607.00003",
                    "https://arxiv.org/abs/2607.00003",
                    "hash-vision",
                    "raw/private-vision.xml",
                    "2026-07-19T12:00:00Z",
                    None,
                ),
            ],
        )
        connection.executemany(
            """INSERT INTO works(
            id, work_type, canonical_title, normalized_title, abstract, publication_status,
            first_published_at, current_version_id, lifecycle_state, created_at, updated_at
            ) VALUES (?, 'paper', ?, ?, ?, 'preprint', ?, ?, 'normalized', ?, ?)""",
            [
                (
                    "work-agent",
                    "Agentic Video Search",
                    "agentic video search",
                    "Self-correcting agents retrieve evidence from long videos.",
                    "2026-07-17T10:00:00Z",
                    "version-agent",
                    "2026-07-17T12:00:00Z",
                    "2026-07-17T12:00:00Z",
                ),
                (
                    "work-local",
                    "Local Model Routing",
                    "local model routing",
                    "A bounded router selects efficient local language models.",
                    "2026-07-18T10:00:00Z",
                    "version-local",
                    "2026-07-18T12:00:00Z",
                    "2026-07-18T12:00:00Z",
                ),
                (
                    "work-vision",
                    "Vision Systems Without Dates",
                    "vision systems without dates",
                    None,
                    None,
                    "version-vision",
                    "2026-07-19T12:00:00Z",
                    "2026-07-19T12:00:00Z",
                ),
            ],
        )
        connection.executemany(
            """INSERT INTO work_versions(
            id, work_id, version_label, title, abstract, source_record_id, published_at,
            observed_at, is_current
            ) VALUES (?, ?, 'v1', ?, ?, ?, ?, ?, 1)""",
            [
                (
                    "version-agent",
                    "work-agent",
                    "Agentic Video Search",
                    "Self-correcting agents retrieve evidence from long videos.",
                    "record-agent",
                    "2026-07-17T10:00:00Z",
                    "2026-07-17T12:00:00Z",
                ),
                (
                    "version-local",
                    "work-local",
                    "Local Model Routing",
                    "A bounded router selects efficient local language models.",
                    "record-local",
                    "2026-07-18T10:00:00Z",
                    "2026-07-18T12:00:00Z",
                ),
                (
                    "version-vision",
                    "work-vision",
                    "Vision Systems Without Dates",
                    None,
                    "record-vision",
                    None,
                    "2026-07-19T12:00:00Z",
                ),
            ],
        )
        connection.executemany(
            """INSERT INTO external_ids(
            id, work_id, id_type, normalized_value, raw_value, source_record_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '2026-07-20T00:00:00Z')""",
            [
                (
                    "external-agent",
                    "work-agent",
                    "arxiv",
                    "2607.00001",
                    "https://arxiv.org/abs/2607.00001v1",
                    "record-agent",
                ),
                (
                    "external-agent-doi",
                    "work-agent",
                    "doi",
                    "10.1234/agent.1",
                    "10.1234/agent.1",
                    "record-agent",
                ),
                (
                    "external-local",
                    "work-local",
                    "arxiv",
                    "2607.00002",
                    "2607.00002v1",
                    "record-local",
                ),
                (
                    "external-vision",
                    "work-vision",
                    "arxiv",
                    "2607.00003",
                    "2607.00003v1",
                    "record-vision",
                ),
            ],
        )
        connection.executemany(
            """INSERT INTO authors(
            id, normalized_name, display_name, created_at, updated_at
            ) VALUES (?, ?, ?, '2026-07-20T00:00:00Z', '2026-07-20T00:00:00Z')""",
            [
                ("author-ada", "ada lovelace", "Ada Lovelace"),
                ("author-grace", "grace hopper", "Grace Hopper"),
                ("author-katherine", "katherine johnson", "Katherine Johnson"),
            ],
        )
        connection.executemany(
            "INSERT INTO work_authors(work_id, author_id, author_order) VALUES (?, ?, ?)",
            [
                ("work-agent", "author-ada", 1),
                ("work-agent", "author-grace", 2),
                ("work-local", "author-katherine", 1),
            ],
        )
        connection.executemany(
            """INSERT INTO topics(id, topic_key, display_name, description)
            VALUES (?, ?, ?, ?)""",
            [
                ("topic-agent", "agentic-systems", "Agentic Systems", "Agents"),
                ("topic-local", "local-models", "Local and Open Models", "Local models"),
                ("topic-vision", "multimodal-systems", "Multimodal Systems", "Vision"),
            ],
        )
        connection.executemany(
            """INSERT INTO work_topics(
            work_id, topic_id, assignment_method, confidence, created_at
            ) VALUES (?, ?, 'rule', 1.0, '2026-07-20T00:00:00Z')""",
            [
                ("work-agent", "topic-agent"),
                ("work-local", "topic-local"),
                ("work-vision", "topic-vision"),
            ],
        )
    repository = SQLiteCatalogReadRepository(connection)
    try:
        yield root, connection, repository
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def test_repository_paginates_filters_searches_and_sorts_deterministically(
    catalog_store: tuple[Path, sqlite3.Connection, SQLiteCatalogReadRepository],
) -> None:
    _, connection, repository = catalog_store

    newest = repository.list_papers(CatalogPaperQuery(limit=1, offset=0))
    assert newest.total == 3 and newest.has_more is True
    assert newest.items[0].id == "work-local"
    second = repository.list_papers(CatalogPaperQuery(limit=1, offset=1))
    assert second.items[0].id == "work-agent"
    searched = repository.list_papers(CatalogPaperQuery(q="self correcting"))
    assert [paper.id for paper in searched.items] == ["work-agent"]
    author_search = repository.list_papers(CatalogPaperQuery(q="Ada"))
    assert [paper.id for paper in author_search.items] == ["work-agent"]
    filtered = repository.list_papers(
        CatalogPaperQuery(
            topic="local-models",
            source="arxiv",
            published_from=date(2026, 7, 18),
            published_to=date(2026, 7, 18),
        )
    )
    assert [paper.id for paper in filtered.items] == ["work-local"]
    titled = repository.list_papers(CatalogPaperQuery(sort=CatalogSort.TITLE))
    assert [paper.id for paper in titled.items] == [
        "work-agent",
        "work-local",
        "work-vision",
    ]
    injection = repository.list_papers(CatalogPaperQuery(q="' OR 1=1 --"))
    assert injection.total == 0
    assert connection.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 3


def test_repository_detail_hydrates_public_fields_without_raw_provenance_paths(
    catalog_store: tuple[Path, sqlite3.Connection, SQLiteCatalogReadRepository],
) -> None:
    repository = catalog_store[2]
    paper = repository.get_paper("work-agent")

    assert paper is not None
    assert [author.display_name for author in paper.authors] == ["Ada Lovelace", "Grace Hopper"]
    assert [topic.key for topic in paper.topics] == ["agentic-systems"]
    assert {identity.id_type.value for identity in paper.identities} == {"arxiv", "doi"}
    assert paper.external_url == "https://arxiv.org/abs/2607.00001"
    serialized = paper.model_dump_json()
    assert "raw/private" not in serialized
    assert "payload" not in serialized
    assert repository.get_paper("missing") is None


async def _exercise_catalog_api(repository: SQLiteCatalogReadRepository) -> None:
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_catalog_repository] = lambda: repository
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        listing = await client.get("/items", params={"q": "local", "limit": 10})
        detail = await client.get("/items/work-local")
        filters = await client.get("/catalog/filters")
        missing = await client.get("/items/missing")
        bad_limit = await client.get("/items", params={"limit": 51})
        bad_offset = await client.get("/items", params={"offset": -1})
        bad_dates = await client.get(
            "/items",
            params={"published_from": "2026-07-20", "published_to": "2026-07-18"},
        )
        bad_search = await client.get("/items", params={"q": "---"})

    assert listing.status_code == 200 and listing.json()["items"][0]["id"] == "work-local"
    assert detail.status_code == 200 and detail.json()["current_version"] == "v1"
    assert filters.status_code == 200 and len(filters.json()["topics"]) == 3
    assert missing.status_code == 404
    assert {
        bad_limit.status_code,
        bad_offset.status_code,
        bad_dates.status_code,
        bad_search.status_code,
    } == {422}


def test_catalog_api_validates_queries_and_exposes_list_detail_and_filters(
    catalog_store: tuple[Path, sqlite3.Connection, SQLiteCatalogReadRepository],
) -> None:
    asyncio.run(_exercise_catalog_api(catalog_store[2]))


def test_catalog_api_redacts_repository_and_sql_failures() -> None:
    async def exercise() -> None:
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_catalog_repository] = FailingCatalogRepository
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/items")

        assert response.status_code == 500
        serialized = response.text
        assert "temporarily unavailable" in serialized
        assert "SELECT" not in serialized
        assert "raw_payload" not in serialized

    asyncio.run(exercise())
