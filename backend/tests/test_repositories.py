"""Focused M1.2 domain and SQLite repository tests."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import REPOSITORY_ROOT
from app.db.connection import SQLiteDatabase, transaction
from app.db.migrations import MigrationRunner
from app.domain.models import (
    AnalysisRun,
    AnalysisRunFilter,
    AnalysisStatus,
    AnalysisType,
    Document,
    DocumentFilter,
    DocumentRole,
    LifecycleState,
    NormalizationStatus,
    PageRequest,
    ParseStatus,
    PipelineRun,
    PipelineRunFilter,
    PipelineRunType,
    PipelineStatus,
    PipelineTriggerType,
    PublicationStatus,
    RankingProfile,
    RankingProfileFilter,
    RankingResult,
    RankingResultFilter,
    RankingScoreKind,
    Source,
    SourceFilter,
    SourceHealth,
    SourceRecord,
    SourceRecordFilter,
    TrustTier,
    Work,
    WorkFilter,
    WorkType,
    WorkVersion,
    WorkVersionFilter,
)
from app.domain.repositories import (
    AnalysisRepository,
    DocumentRepository,
    PipelineRunRepository,
    RankingRepository,
    SourceRecordRepository,
    SourceRepository,
    WorkRepository,
    WorkVersionRepository,
)
from app.repositories.sqlite import (
    RepositoryConstraintError,
    RepositoryDuplicateError,
    RepositoryTransactionError,
    SQLiteRepositories,
)

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 17, 11, 0, tzinfo=UTC)


@pytest.fixture
def repositories() -> Iterator[tuple[sqlite3.Connection, SQLiteRepositories]]:
    root = REPOSITORY_ROOT / "data" / ".test-sqlite" / uuid4().hex
    root.mkdir(parents=True)
    database = SQLiteDatabase(path=root / "repositories.db")
    MigrationRunner(database).migrate()
    connection = database.connect()
    try:
        yield connection, SQLiteRepositories.for_connection(connection)
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def _source(identifier: str = "source-1", *, enabled: bool = True) -> Source:
    return Source(
        id=identifier,
        source_key=f"key-{identifier}",
        display_name=f"Source {identifier}",
        trust_tier=TrustTier.A,
        base_url="https://example.test",
        enabled=enabled,
        poll_interval_minutes=60,
        minimum_request_interval_ms=1000,
        connector_version="fixture-v1",
        config={"feed": "all"},
        created_at=NOW,
        updated_at=NOW,
    )


def _record(identifier: str = "record-1", source_id: str = "source-1") -> SourceRecord:
    return SourceRecord(
        id=identifier,
        source_id=source_id,
        upstream_id=f"upstream-{identifier}",
        canonical_url=f"https://example.test/{identifier}",
        payload_sha256=f"sha-{identifier}",
        raw_payload_path=f"raw/{identifier}.json",
        observed_at=NOW,
    )


def _work(identifier: str = "work-1", work_type: WorkType = WorkType.PAPER) -> Work:
    return Work(
        id=identifier,
        work_type=work_type,
        canonical_title=f"Title {identifier}",
        normalized_title=f"title {identifier}",
        created_at=NOW,
        updated_at=NOW,
    )


def _version(identifier: str = "version-1", work_id: str = "work-1") -> WorkVersion:
    return WorkVersion(
        id=identifier,
        work_id=work_id,
        version_label="v1",
        title=f"Version {identifier}",
        metadata={"source": "fixture"},
        observed_at=NOW,
    )


def _document(identifier: str = "document-1", version_id: str = "version-1") -> Document:
    return Document(
        id=identifier,
        work_version_id=version_id,
        document_role=DocumentRole.PAPER_PDF,
        source_url=f"https://example.test/{identifier}.pdf",
        local_path=f"documents/{identifier}.pdf",
        media_type="application/pdf",
        byte_size=100,
        sha256=f"sha-{identifier}",
        acquired_at=NOW,
    )


def _seed_work_graph(connection: sqlite3.Connection, repos: SQLiteRepositories) -> None:
    with transaction(connection):
        repos.sources.create(_source())
        repos.source_records.create_or_get(_record())
        repos.works.create(_work())
        repos.work_versions.create_or_get(_version())


def test_repository_implementations_satisfy_typed_boundaries(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    _, repos = repositories
    boundaries: tuple[object, ...] = (
        repos.sources,
        repos.source_records,
        repos.works,
        repos.work_versions,
        repos.documents,
        repos.rankings,
        repos.analyses,
        repos.pipeline_runs,
    )
    source: SourceRepository = repos.sources
    record: SourceRecordRepository = repos.source_records
    work: WorkRepository = repos.works
    version: WorkVersionRepository = repos.work_versions
    document: DocumentRepository = repos.documents
    ranking: RankingRepository = repos.rankings
    analysis: AnalysisRepository = repos.analyses
    pipeline: PipelineRunRepository = repos.pipeline_runs

    typed_boundaries: tuple[object, ...] = (
        source,
        record,
        work,
        version,
        document,
        ranking,
        analysis,
        pipeline,
    )
    assert typed_boundaries == boundaries
    for path in (REPOSITORY_ROOT / "backend" / "app" / "domain").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "fastapi" not in text.lower()
        assert "import app.connectors" not in text.lower()
        assert "from app.connectors" not in text.lower()


def test_domain_models_reject_invalid_values() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _source().model_copy(update={"created_at": datetime(2026, 7, 17)}).model_validate(
            _source().model_copy(update={"created_at": datetime(2026, 7, 17)}).model_dump()
        )
    with pytest.raises(ValidationError):
        PageRequest(limit=101)
    with pytest.raises(ValidationError):
        RankingResult(
            id="rank-invalid",
            work_id="work-1",
            profile_id="profile-1",
            score_kind=RankingScoreKind.TECHNICAL,
            total_score=101,
            components={},
            feature_snapshot={},
            calculated_at=NOW,
        )


def test_sources_create_read_update_filter_paginate_and_constrain(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    with transaction(connection):
        repos.sources.create(_source("source-a"))
        repos.sources.create(_source("source-b", enabled=False))
        repos.sources.create(_source("source-c"))

    updated = _source("source-a").model_copy(
        update={"health_status": SourceHealth.HEALTHY, "updated_at": LATER}
    )
    with transaction(connection):
        repos.sources.update(updated)

    assert repos.sources.get("source-a") == updated
    assert [item.id for item in repos.sources.list(PageRequest(limit=1, offset=1))] == ["source-b"]
    enabled = repos.sources.list(PageRequest(), SourceFilter(enabled=True))
    assert [item.id for item in enabled] == ["source-a", "source-c"]

    with pytest.raises(RepositoryDuplicateError), transaction(connection):
        repos.sources.create(_source("source-x").model_copy(update={"source_key": "key-source-a"}))
    assert repos.sources.get("source-x") is None


def test_source_records_deduplicate_update_filter_and_enforce_foreign_keys(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    with transaction(connection):
        repos.sources.create(_source())
        first = repos.source_records.create_or_get(_record())
        duplicate = repos.source_records.create_or_get(
            _record("another-id").model_copy(
                update={
                    "upstream_id": first.entity.upstream_id,
                    "payload_sha256": first.entity.payload_sha256,
                }
            )
        )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.entity.id == "record-1"

    updated = _record().model_copy(update={"normalization_status": NormalizationStatus.NORMALIZED})
    with transaction(connection):
        repos.source_records.update(updated)
    assert repos.source_records.get("record-1") == updated
    assert repos.source_records.list(
        PageRequest(), SourceRecordFilter(normalization_status=NormalizationStatus.NORMALIZED)
    ) == (updated,)

    with pytest.raises(RepositoryConstraintError), transaction(connection):
        repos.source_records.create_or_get(_record("orphan", "missing-source"))


def test_works_versions_and_documents_round_trip_update_deduplicate_and_filter(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    _seed_work_graph(connection, repos)
    document = _document()
    with transaction(connection):
        created_document = repos.documents.create_or_get(document)
        duplicate_document = repos.documents.create_or_get(
            _document("duplicate").model_copy(update={"sha256": document.sha256})
        )

    assert repos.works.get("work-1") == _work()
    assert repos.work_versions.get("version-1") == _version()
    assert created_document.created and not duplicate_document.created
    assert duplicate_document.entity.id == document.id

    updated_work = _work().model_copy(
        update={
            "publication_status": PublicationStatus.PUBLISHED,
            "lifecycle_state": LifecycleState.PARSED,
        }
    )
    updated_version = _version().model_copy(update={"abstract": "Updated abstract"})
    updated_document = document.model_copy(
        update={"parse_status": ParseStatus.PARSED, "page_count": 4, "parsed_at": LATER}
    )
    with transaction(connection):
        repos.works.update(updated_work)
        repos.work_versions.update(updated_version)
        repos.documents.update(updated_document)

    assert repos.works.list(
        PageRequest(), WorkFilter(publication_status=PublicationStatus.PUBLISHED)
    ) == (updated_work,)
    assert repos.work_versions.list(PageRequest(), WorkVersionFilter(work_id="work-1")) == (
        updated_version,
    )
    assert repos.documents.list(PageRequest(), DocumentFilter(parse_status=ParseStatus.PARSED)) == (
        updated_document,
    )

    with transaction(connection):
        duplicate_version = repos.work_versions.create_or_get(
            _version("different-id").model_copy(update={"title": "Ignored duplicate"})
        )
    assert not duplicate_version.created and duplicate_version.entity.id == "version-1"


def test_ranking_profile_and_result_create_update_deduplicate_paginate_and_filter(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    with transaction(connection):
        repos.works.create(_work())
        profile = repos.rankings.create_profile(
            RankingProfile(
                id="profile-1",
                profile_key="technical",
                version=1,
                weights={"novelty": 1.0},
                normalization={"method": "bounded"},
                created_at=NOW,
            )
        )
        result = RankingResult(
            id="ranking-1",
            work_id="work-1",
            profile_id=profile.id,
            score_kind=RankingScoreKind.TECHNICAL,
            total_score=80,
            components={"novelty": 80},
            feature_snapshot={"citations": 2},
            calculated_at=NOW,
        )
        created = repos.rankings.create_result_or_get(result)
        duplicate = repos.rankings.create_result_or_get(
            result.model_copy(update={"id": "ranking-2"})
        )

    assert created.created and not duplicate.created
    updated_profile = profile.model_copy(update={"active": False})
    updated_result = result.model_copy(update={"total_score": 85.5})
    with transaction(connection):
        repos.rankings.update_profile(updated_profile)
        repos.rankings.update_result(updated_result)

    assert repos.rankings.get_profile(profile.id) == updated_profile
    assert repos.rankings.get_result(result.id) == updated_result
    assert repos.rankings.list_profiles(PageRequest(), RankingProfileFilter(active=False)) == (
        updated_profile,
    )
    assert repos.rankings.list_results(
        PageRequest(limit=1), RankingResultFilter(score_kind=RankingScoreKind.TECHNICAL)
    ) == (updated_result,)


def test_analysis_runs_deduplicate_nullable_identity_update_and_filter(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    _seed_work_graph(connection, repos)
    run = AnalysisRun(
        id="analysis-1",
        work_id="work-1",
        work_version_id="version-1",
        analysis_type=AnalysisType.FAST_BRIEF,
        status=AnalysisStatus.QUEUED,
        input_fingerprint="input-sha",
        created_at=NOW,
    )
    with transaction(connection):
        first = repos.analyses.create_or_get(run)
        duplicate = repos.analyses.create_or_get(run.model_copy(update={"id": "analysis-2"}))
    assert first.created and not duplicate.created

    updated = run.model_copy(
        update={
            "status": AnalysisStatus.SUCCEEDED,
            "started_at": NOW,
            "completed_at": LATER,
            "duration_ms": 3_600_000,
            "output": {"summary": "done"},
        }
    )
    with transaction(connection):
        repos.analyses.update(updated)
    assert repos.analyses.get(run.id) == updated
    assert repos.analyses.list(
        PageRequest(),
        AnalysisRunFilter(analysis_type=AnalysisType.FAST_BRIEF, status=AnalysisStatus.SUCCEEDED),
    ) == (updated,)


def test_pipeline_runs_create_read_update_filter_and_paginate(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    runs = tuple(
        PipelineRun(
            id=f"pipeline-{index}",
            run_type=PipelineRunType.DAILY,
            trigger_type=PipelineTriggerType.MANUAL,
            status=PipelineStatus.QUEUED,
            config_snapshot={"index": index},
            queued_at=NOW,
        )
        for index in range(3)
    )
    with transaction(connection):
        for run in runs:
            repos.pipeline_runs.create(run)
    updated = runs[0].model_copy(update={"status": PipelineStatus.SUCCEEDED, "completed_at": LATER})
    with transaction(connection):
        repos.pipeline_runs.update(updated)

    assert repos.pipeline_runs.get(updated.id) == updated
    assert repos.pipeline_runs.list(
        PageRequest(), PipelineRunFilter(status=PipelineStatus.SUCCEEDED)
    ) == (updated,)
    assert len(repos.pipeline_runs.list(PageRequest(limit=1, offset=1))) == 1


def test_shared_transaction_rolls_back_cross_repository_writes(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    connection, repos = repositories
    with pytest.raises(RuntimeError, match="abort"), transaction(connection):
        repos.sources.create(_source())
        repos.works.create(_work())
        raise RuntimeError("abort unit of work")

    assert repos.sources.get("source-1") is None
    assert repos.works.get("work-1") is None


def test_writes_require_explicit_transaction(
    repositories: tuple[sqlite3.Connection, SQLiteRepositories],
) -> None:
    _, repos = repositories
    with pytest.raises(RepositoryTransactionError, match="explicit transaction"):
        repos.sources.create(_source())
