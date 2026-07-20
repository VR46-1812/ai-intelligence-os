"""Focused M1.3 catalog identity and revision tests."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

import pytest

from app.catalog.identity import (
    CatalogIdentityError,
    CatalogIdentityService,
    CatalogRecord,
    IdentityInput,
    IdentityResolutionStatus,
    normalize_identifier,
)
from app.config import REPOSITORY_ROOT
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import MigrationRunner
from app.domain.models import (
    ExternalIdType,
    PageRequest,
    PublicationStatus,
    WorkType,
    WorkVersionFilter,
)
from app.repositories.sqlite import SQLiteRepositories

NOW = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
FIXTURES = Path(__file__).with_name("fixtures")


class _IdentityCase(TypedDict):
    id_type: str
    raw_value: str
    normalized_value: str
    version_label: str | None


@pytest.fixture
def catalog() -> Iterator[tuple[sqlite3.Connection, SQLiteRepositories]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(path=root / "catalog.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    try:
        yield connection, SQLiteRepositories.for_connection(connection)
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def _id_factory() -> Callable[[], str]:
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"test-id-{counter:04d}"

    return next_id


def _service(repositories: SQLiteRepositories) -> CatalogIdentityService:
    return CatalogIdentityService(
        repositories.works,
        repositories.work_versions,
        repositories.catalog_identities,
        id_factory=_id_factory(),
        clock=lambda: NOW,
    )


def _record(
    *,
    raw_identity: str = "arXiv:2401.12345v1",
    id_type: ExternalIdType = ExternalIdType.ARXIV,
    title: str = "Reliable Local Intelligence Systems",
    author: str = "Ada Lovelace",
    published_at: datetime = NOW,
    version: str = "v1",
    content_sha256: str = "content-v1",
) -> CatalogRecord:
    return CatalogRecord(
        work_type=WorkType.PAPER,
        title=title,
        abstract="A fixture abstract.",
        publication_status=PublicationStatus.PREPRINT,
        published_at=published_at,
        observed_at=NOW,
        upstream_version=version,
        content_sha256=content_sha256,
        first_author=author,
        identities=(IdentityInput(id_type=id_type, raw_value=raw_identity),),
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    allowed = {"works", "work_versions", "external_ids", "authors", "work_authors"}
    if table not in allowed:
        raise AssertionError(f"unexpected fixture table: {table}")
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_doi_arxiv_and_openreview_normalization_fixtures() -> None:
    cases = cast(
        list[_IdentityCase],
        json.loads((FIXTURES / "catalog_identity_cases.json").read_text(encoding="utf-8")),
    )
    for case in cases:
        normalized = normalize_identifier(
            IdentityInput(id_type=ExternalIdType(case["id_type"]), raw_value=case["raw_value"])
        )
        assert normalized.normalized_value == case["normalized_value"]
        assert normalized.version_label == case["version_label"]


@pytest.mark.parametrize(
    ("id_type", "raw_value"),
    [
        (ExternalIdType.DOI, "not-a-doi"),
        (ExternalIdType.ARXIV, "2401.invalid"),
        (ExternalIdType.OPENREVIEW, "https://attacker.test/forum?id=abc"),
        (ExternalIdType.GITHUB, "https://github.com/example/project"),
    ],
)
def test_identifier_normalization_rejects_malformed_or_out_of_scope_values(
    id_type: ExternalIdType, raw_value: str
) -> None:
    with pytest.raises(CatalogIdentityError):
        normalize_identifier(IdentityInput(id_type=id_type, raw_value=raw_value))


def test_exact_reingestion_is_idempotent_and_external_identity_round_trips(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    service = _service(repositories)
    record = _record()
    with transaction(connection):
        created = service.resolve(record)
    with transaction(connection):
        repeated = service.resolve(record)

    assert created.status is IdentityResolutionStatus.CREATED
    assert repeated.status is IdentityResolutionStatus.ALREADY_KNOWN
    assert repeated.work_id == created.work_id
    assert repeated.version_id == created.version_id
    assert _count(connection, "works") == 1
    assert _count(connection, "work_versions") == 1
    assert _count(connection, "external_ids") == 1
    identifier = repositories.catalog_identities.get_external_id(ExternalIdType.ARXIV, "2401.12345")
    assert identifier is not None and identifier.work_id == created.work_id
    assert repositories.catalog_identities.list_external_ids(created.work_id or "") == (identifier,)


def test_arxiv_revision_preserves_work_and_switches_current_version(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    service = _service(repositories)
    with transaction(connection):
        first = service.resolve(_record())
    with transaction(connection):
        revision = service.resolve(
            _record(
                raw_identity="https://arxiv.org/abs/2401.12345v2",
                version="v2",
                content_sha256="content-v2",
            )
        )

    assert revision.status is IdentityResolutionStatus.REVISION_CREATED
    assert revision.work_id == first.work_id
    versions = repositories.work_versions.list(
        PageRequest(), WorkVersionFilter(work_id=first.work_id)
    )
    assert {version.version_label for version in versions} == {"v1", "v2"}
    assert [version.version_label for version in versions if version.is_current] == ["v2"]
    work = repositories.works.get(first.work_id or "")
    assert work is not None and work.current_version_id == revision.version_id


def test_conservative_fingerprint_candidate_requires_manual_review_without_writes(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    service = _service(repositories)
    with transaction(connection):
        created = service.resolve(_record())
    candidate = _record(
        raw_identity="https://openreview.net/forum?id=different-id",
        id_type=ExternalIdType.OPENREVIEW,
    )
    with transaction(connection):
        resolution = service.resolve(candidate)

    assert resolution.status is IdentityResolutionStatus.MANUAL_REVIEW
    assert resolution.candidate_work_ids == (created.work_id,)
    assert _count(connection, "works") == 1
    assert _count(connection, "external_ids") == 1


def test_below_threshold_title_never_auto_merges(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    service = _service(repositories)
    with transaction(connection):
        first = service.resolve(_record())
    with transaction(connection):
        unrelated = service.resolve(
            _record(
                raw_identity="https://openreview.net/forum?id=unrelated-id",
                id_type=ExternalIdType.OPENREVIEW,
                title="A Completely Different Study of Marine Biology",
            )
        )

    assert unrelated.status is IdentityResolutionStatus.CREATED
    assert unrelated.work_id != first.work_id
    assert _count(connection, "works") == 2


def test_conflicting_exact_identifiers_enter_manual_review(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    service = _service(repositories)
    with transaction(connection):
        first = service.resolve(_record())
    with transaction(connection):
        second = service.resolve(
            _record(
                raw_identity="https://openreview.net/forum?id=second-work",
                id_type=ExternalIdType.OPENREVIEW,
                title="Second Work",
                author="Grace Hopper",
            )
        )
    conflict = _record().model_copy(
        update={
            "identities": (
                IdentityInput(id_type=ExternalIdType.ARXIV, raw_value="2401.12345"),
                IdentityInput(
                    id_type=ExternalIdType.OPENREVIEW,
                    raw_value="second-work",
                ),
            )
        }
    )
    with transaction(connection):
        resolution = service.resolve(conflict)

    assert resolution.status is IdentityResolutionStatus.MANUAL_REVIEW
    assert resolution.candidate_work_ids == tuple(
        sorted((first.work_id or "", second.work_id or ""))
    )
    assert _count(connection, "works") == 2


def test_dependency_failure_rolls_back_partial_catalog_creation(
    catalog: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repositories = catalog
    calls = 0

    def failing_ids() -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("id allocation failed")
        return f"failure-id-{calls}"

    service = CatalogIdentityService(
        repositories.works,
        repositories.work_versions,
        repositories.catalog_identities,
        id_factory=failing_ids,
        clock=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="id allocation failed"), transaction(connection):
        service.resolve(_record())

    for table in ("works", "work_versions", "external_ids", "authors", "work_authors"):
        assert _count(connection, table) == 0
