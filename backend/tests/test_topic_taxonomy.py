"""Focused M1.4 controlled taxonomy tests."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.catalog.taxonomy import (
    SourceCategoryMapping,
    TaxonomyError,
    TopicDefinition,
    TopicTaxonomy,
    TopicTaxonomyService,
    load_default_taxonomy,
)
from app.config import REPOSITORY_ROOT, AppSettings, PathSettings
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import MigrationRunner
from app.domain.repositories import TopicRepository
from app.main import create_app
from app.repositories.sqlite import RepositoryTransactionError, SQLiteRepositories


@pytest.fixture
def topic_store() -> Iterator[tuple[sqlite3.Connection, SQLiteRepositories]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(path=root / "topics.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    try:
        yield connection, SQLiteRepositories.for_connection(connection)
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def test_default_taxonomy_is_versioned_and_aligned_with_context_priorities() -> None:
    taxonomy = load_default_taxonomy()
    keys = {topic.topic_key for topic in taxonomy.topics}

    assert taxonomy.schema_version == 1
    assert taxonomy.taxonomy_version == "2026.1"
    assert {
        "unknown",
        "agentic-systems",
        "retrieval-intelligence",
        "local-models",
        "ai-quality-security",
        "multimodal-systems",
        "education-ai",
        "tender-intelligence",
        "financial-intelligence",
        "real-estate-automation",
        "indian-smb-workflows",
    } <= keys
    assert set(taxonomy.user_weights) == keys


def test_seed_is_idempotent_and_preserves_parent_relationships(
    topic_store: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = topic_store
    boundary: TopicRepository = repositories.topics
    service = TopicTaxonomyService(boundary, load_default_taxonomy())
    with transaction(connection):
        first = service.seed()
    with transaction(connection):
        repeated = service.seed()

    assert first.created == 13 and first.updated == 0
    assert repeated.created == 0 and repeated.updated == 13
    assert len(repositories.topics.list()) == 13
    parent = repositories.topics.get_by_key("ai-systems")
    child = repositories.topics.get_by_key("agentic-systems")
    assert parent is not None and child is not None
    assert child.parent_topic_id == parent.id


def test_arxiv_category_mapping_is_deterministic_and_unknown_is_explicit(
    topic_store: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    _, repositories = topic_store
    service = TopicTaxonomyService(repositories.topics, load_default_taxonomy())

    matches = service.map_source_category("ARXIV", " cs.CL ")
    assert [(match.topic_key, match.user_weight) for match in matches] == [
        ("retrieval-intelligence", 0.95),
        ("local-models", 0.9),
    ]
    unknown = service.map_source_category("arxiv", "quant-ph")
    assert [(match.topic_key, match.user_weight) for match in unknown] == [("unknown", 0.0)]


def test_user_weights_are_bounded_and_unknown_keys_fail_explicitly(
    topic_store: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    _, repositories = topic_store
    service = TopicTaxonomyService(repositories.topics, load_default_taxonomy())

    assert service.user_weight("agentic-systems") == 1.0
    with pytest.raises(TaxonomyError, match="unknown controlled topic"):
        service.user_weight("not-controlled")


def test_new_taxonomy_version_updates_stable_topic_rows(
    topic_store: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = topic_store
    original = load_default_taxonomy()
    service = TopicTaxonomyService(repositories.topics, original)
    with transaction(connection):
        service.seed()
    updated_topics = tuple(
        topic.model_copy(update={"display_name": "Agent Systems"})
        if topic.topic_key == "agentic-systems"
        else topic
        for topic in original.topics
    )
    next_version = original.model_copy(
        update={"taxonomy_version": "2026.2", "topics": updated_topics}
    )
    with transaction(connection):
        result = TopicTaxonomyService(repositories.topics, next_version).seed()

    topic = repositories.topics.get_by_key("agentic-systems")
    assert result.taxonomy_version == "2026.2"
    assert result.created == 0 and result.updated == 13
    assert topic is not None and topic.display_name == "Agent Systems"
    assert topic.id == "topic:agentic-systems"


def test_taxonomy_validation_rejects_bad_references_weights_and_cycles() -> None:
    topic = TopicDefinition(topic_key="unknown", display_name="Unknown", description="Fallback")
    valid = {
        "schema_version": 1,
        "taxonomy_version": "2026.1",
        "topics": [topic.model_dump()],
        "source_category_mappings": [],
        "user_weights": {"unknown": 0.0},
    }
    with pytest.raises(ValidationError, match="unknown topics"):
        TopicTaxonomy.model_validate(
            {
                **valid,
                "source_category_mappings": [
                    SourceCategoryMapping(
                        source_key="arxiv",
                        source_category="cs.AI",
                        topic_keys=("missing",),
                    ).model_dump()
                ],
            }
        )
    with pytest.raises(ValidationError, match="exactly every controlled topic"):
        TopicTaxonomy.model_validate({**valid, "user_weights": {}})
    with pytest.raises(ValidationError, match="parent cycle"):
        TopicTaxonomy.model_validate(
            {
                **valid,
                "topics": [
                    {
                        **topic.model_dump(),
                        "parent_topic_key": "unknown",
                    }
                ],
            }
        )


def test_seed_rolls_back_and_requires_caller_transaction(
    topic_store: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = topic_store
    service = TopicTaxonomyService(repositories.topics, load_default_taxonomy())
    with pytest.raises(RepositoryTransactionError, match="explicit transaction"):
        service.seed()
    with pytest.raises(RuntimeError, match="abort"), transaction(connection):
        service.seed()
        raise RuntimeError("abort taxonomy seed")

    assert repositories.topics.list() == ()


def test_application_startup_seeds_controlled_topics() -> None:
    relative_root = f"data/.test-taxonomy-startup/{uuid4().hex}"
    settings = AppSettings(
        paths=PathSettings(
            data_root=Path(relative_root),
            database_path=Path("state/startup.db"),
        )
    )
    application = create_app(settings)

    async def run_lifespan() -> None:
        async with application.router.lifespan_context(application):
            pass

    try:
        asyncio.run(run_lifespan())
        connection = SQLiteDatabase(settings.paths.database_path).connect()
        try:
            count = int(connection.execute("SELECT count(*) FROM topics").fetchone()[0])
        finally:
            connection.close()
        assert count == 13
    finally:
        shutil.rmtree(settings.paths.data_root, ignore_errors=True)
