"""Deterministic fake-Ollama tests for bounded local Scout analysis."""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest

from app.analysis.models import ModelStatus
from app.analysis.ollama import OllamaClient, OllamaGeneration
from app.analysis.service import ScoutAnalysisService
from app.config import REPOSITORY_ROOT, AppSettings, PathSettings, initialize_directories
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.domain.models import AnalysisStatus, AnalysisType
from app.repositories import SQLiteRepositories


class FakeScout:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def status(self, model: str) -> ModelStatus:
        return ModelStatus(
            available=True,
            model=model,
            model_installed=True,
            runtime_version="fixture",
            detail="Fixture Scout is ready.",
        )

    async def generate(self, **_: object) -> OllamaGeneration:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return OllamaGeneration(response, 12, 100, 80)


@pytest.fixture
def analysis_store() -> Iterator[tuple[AppSettings, sqlite3.Connection]]:
    root = REPOSITORY_ROOT / "data" / ".test-analysis" / uuid4().hex
    settings = AppSettings(paths=PathSettings(data_root=root))
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path)
    MigrationRunner(database).migrate()
    connection = database.connect()
    with transaction(connection):
        connection.execute(
            """INSERT INTO sources(id,source_key,display_name,trust_tier,base_url,
            poll_interval_minutes,
            connector_version,created_at,updated_at) VALUES
            ('source','arxiv','arXiv','A','https://export.arxiv.org',60,'v1',
            '2026-07-20T00:00:00Z','2026-07-20T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO source_records(id,source_id,upstream_id,canonical_url,payload_sha256,
            raw_payload_path,observed_at,normalization_status) VALUES
            ('record','source','2607.1','https://arxiv.org/abs/2607.1','hash','raw/x',
            '2026-07-20T00:00:00Z','normalized')"""
        )
        connection.execute(
            """INSERT INTO works(id,work_type,canonical_title,normalized_title,publication_status,
            current_version_id,lifecycle_state,created_at,updated_at) VALUES
            ('work','paper','Local Agents','local agents','preprint','version','parsed',
            '2026-07-20T00:00:00Z','2026-07-20T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO work_versions(
            id,work_id,version_label,title,source_record_id,observed_at,
            is_current) VALUES ('version','work','v1','Local Agents','record',
            '2026-07-20T00:00:00Z',1)"""
        )
        connection.execute(
            """INSERT INTO documents(id,work_version_id,document_role,source_url,local_path,
            media_type,byte_size,sha256,parse_status,acquired_at) VALUES
            ('document','version','paper_pdf','https://arxiv.org/pdf/2607.1','raw/p.pdf',
            'application/pdf',100,'dochash','parsed','2026-07-20T00:00:00Z')"""
        )
        connection.executemany(
            """INSERT INTO evidence_spans(id,document_id,page_start,page_end,char_start,char_end,
            span_text,normalized_text_sha256,created_at) VALUES
            (?,'document',?,?,0,100,?,?, '2026-07-20T00:00:00Z')""",
            [
                ("ev-1", 1, 1, "The method uses a bounded agent loop.", "evhash1"),
                ("ev-2", 2, 2, "Evaluation reports lower tool-call errors.", "evhash2"),
                ("ev-3", 3, 3, "The paper lists compute limitations.", "evhash3"),
            ],
        )
        connection.execute(
            """INSERT INTO ranking_profiles(id,profile_key,version,weights_json,
            normalization_json,active,created_at) VALUES
            ('rank-profile','default',1,'{}','{}',1,'2026-07-20T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO ranking_results(id,work_id,profile_id,score_kind,total_score,
            components_json,feature_snapshot_json,calculated_at) VALUES
            ('rank','work','rank-profile','technical',75,'{}','{}','2026-07-20T00:00:00Z')"""
        )
    try:
        yield settings, connection
    finally:
        connection.close()
        shutil.rmtree(root, ignore_errors=True)


def _brief(evidence_id: str = "ev-1") -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "work_id": "work",
            "change": "A bounded agent loop is evaluated.",
            "problem": "Tool-call errors affect reliability.",
            "contribution": "The paper presents a bounded loop.",
            "evidence_state": "moderate",
            "limitations": ["Only reported paper evidence was reviewed."],
            "code_state": "unknown",
            "technical_relevance": "Relevant to agent reliability.",
            "commercial_relevance": "Requires customer validation.",
            "recommended_action": "read_source",
            "claims": [
                {
                    "text": "The method uses a bounded loop.",
                    "type": "fact",
                    "evidence_ids": [evidence_id],
                },
                {
                    "text": "Commercial fit needs validation.",
                    "type": "hypothesis",
                    "evidence_ids": [],
                },
            ],
        }
    )


def _deep_dive() -> str:
    section = {
        "markdown": "The supplied evidence describes a bounded agent method.",
        "confidence": 0.75,
        "claim_ids": ["claim-1"],
    }
    return json.dumps(
        {
            "schema_version": "1.0",
            "work_id": "work",
            "title": "Local Agents",
            "publication_status": "preprint",
            "executive_significance": section,
            "problem_context": section,
            "method": section,
            "evaluation": section,
            "limitations": section,
            "reproducibility": {
                "status": "unknown",
                "repository_urls": [],
                "assets": [],
                "hardware_fit": "unknown",
                "steps": [],
                "risks": ["No repository evidence was supplied."],
            },
            "production_applications": [],
            "commercial_hypotheses": [],
            "learning_path": [],
            "skeptic_findings": [
                {
                    "severity": "warning",
                    "finding": "Broader evaluation is unknown.",
                    "affected_claim_ids": ["claim-1"],
                    "resolution": "qualified",
                }
            ],
            "claims": [
                {
                    "id": "claim-1",
                    "text": "The evidence describes a bounded agent loop.",
                    "type": "fact",
                    "importance": "major",
                    "verification_status": "supported",
                    "evidence_ids": ["ev-1"],
                    "qualifier": None,
                }
            ],
        }
    )


def test_analysis_is_citation_verified_persisted_and_idempotent(
    analysis_store: tuple[AppSettings, sqlite3.Connection],
) -> None:
    settings, connection = analysis_store
    scout = FakeScout([_brief()])
    service = ScoutAnalysisService(
        connection, SQLiteRepositories.for_connection(connection), scout, settings
    )

    first = asyncio.run(service.analyze("work", AnalysisType.FAST_BRIEF))
    cached = asyncio.run(service.analyze("work", AnalysisType.FAST_BRIEF))

    assert first.status is AnalysisStatus.SUCCEEDED and first.citation_coverage == 1
    assert first.citations_verified == 1 and cached.cached is True
    assert scout.calls == 1
    assert connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 1
    serialized = first.model_dump_json()
    assert "INPUT_DATA" not in serialized and str(settings.paths.data_root) not in serialized


def test_invalid_or_unsupported_output_gets_exactly_one_repair(
    analysis_store: tuple[AppSettings, sqlite3.Connection],
) -> None:
    settings, connection = analysis_store
    scout = FakeScout([_brief("invented-evidence"), _brief("ev-2")])
    service = ScoutAnalysisService(
        connection, SQLiteRepositories.for_connection(connection), scout, settings
    )

    result = asyncio.run(service.analyze("work", AnalysisType.FAST_BRIEF))

    assert result.status is AnalysisStatus.SUCCEEDED
    assert result.citations_verified == 1 and scout.calls == 2


def test_second_invalid_response_is_stored_as_safe_failure(
    analysis_store: tuple[AppSettings, sqlite3.Connection],
) -> None:
    settings, connection = analysis_store
    scout = FakeScout(["{}", "{}"])
    service = ScoutAnalysisService(
        connection, SQLiteRepositories.for_connection(connection), scout, settings
    )

    result = asyncio.run(service.analyze("work", AnalysisType.FAST_BRIEF))

    assert result.status is AnalysisStatus.FAILED and scout.calls == 2
    assert result.error_code == "STRUCTURED_OUTPUT_INVALID"
    assert result.output is None and "prompt" not in (result.safe_detail or "").casefold()


def test_deep_dive_contract_and_claim_links_are_persisted(
    analysis_store: tuple[AppSettings, sqlite3.Connection],
) -> None:
    settings, connection = analysis_store
    service = ScoutAnalysisService(
        connection,
        SQLiteRepositories.for_connection(connection),
        FakeScout([_deep_dive()]),
        settings,
    )

    result = asyncio.run(service.analyze("work", AnalysisType.DEEP_DIVE))

    assert result.status is AnalysisStatus.SUCCEEDED
    assert result.citation_coverage == 1 and result.citations_verified == 1
    assert connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM claim_evidence").fetchone()[0] == 1


class TrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.active_generations = 0
        self.maximum_generations = 0
        self.payloads: list[dict[str, object]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "fixture"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        self.payloads.append(json.loads(request.content))
        self.active_generations += 1
        self.maximum_generations = max(self.maximum_generations, self.active_generations)
        await asyncio.sleep(0.02)
        self.active_generations -= 1
        return httpx.Response(
            200,
            json={"response": "{}", "total_duration": 1_000_000, "eval_count": 2},
        )


def test_ollama_client_is_local_bounded_sequential_and_unloadable(
    analysis_store: tuple[AppSettings, sqlite3.Connection],
) -> None:
    settings = analysis_store[0]
    transport = TrackingTransport()

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            generator = OllamaClient(
                client,
                base_url="http://127.0.0.1:11434",
                generation_semaphore=asyncio.Semaphore(1),
                resources=settings.resources,
            )
            await asyncio.gather(
                generator.generate(
                    prompt="one", schema={"type": "object"}, profile=settings.models.scout
                ),
                generator.generate(
                    prompt="two", schema={"type": "object"}, profile=settings.models.scout
                ),
            )

    asyncio.run(exercise())
    assert transport.maximum_generations == 1
    assert all(
        payload["keep_alive"] == 0 and payload["think"] is False for payload in transport.payloads
    )
    assert all(payload["options"]["num_ctx"] == 8192 for payload in transport.payloads)  # type: ignore[index]
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient(
            httpx.AsyncClient(),
            base_url="https://cloud.example",
            generation_semaphore=asyncio.Semaphore(1),
            resources=settings.resources,
        )
