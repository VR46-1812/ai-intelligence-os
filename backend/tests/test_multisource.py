"""Deterministic V1.1 connector, trust, and linked-event tests."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.config import AppSettings, PathSettings, initialize_directories
from app.db import MigrationRunner, SQLiteDatabase, transaction
from app.domain.models import NormalizationStatus, PublicationStatus, SourceRecord, TrustTier
from app.ingestion.contracts import ConnectorPage, FetchWindow, HttpResponse, RawSourceRecord
from app.multisource.service import LinkedEventReader, MultiSourceDiscoveryService
from app.repositories import SQLiteRepositories
from app.sources.multisource import (
    GitHubConnector,
    HuggingFaceConnector,
    OpenReviewConnector,
    RssAtomConnector,
)

FIXTURES = Path(__file__).parent / "fixtures" / "multisource"
NOW = datetime(2026, 7, 22, tzinfo=UTC)


class FixtureHttp:
    def __init__(self, payloads: dict[str, tuple[str, bytes]]) -> None:
        self.payloads = payloads
        self.requests: list[tuple[str, str, int]] = []

    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: tuple[str, ...],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        del headers
        self.requests.append((source_key, url, minimum_request_interval_ms))
        match = next((value for key, value in self.payloads.items() if key in url), None)
        if match is None:
            raise AssertionError(f"unexpected fixture URL: {url}")
        media_type, content = match
        assert media_type in expected_media_types
        return HttpResponse(
            status_code=200,
            media_type=media_type,
            content=content,
            response_metadata={"etag": '"fixture"'},
        )


def _window(size: int = 5) -> FetchWindow:
    return FetchWindow(
        since=datetime(2026, 7, 15, tzinfo=UTC),
        until=NOW,
        page_size=size,
    )


class FetchingConnector(Protocol):
    def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]: ...


async def _one(connector: FetchingConnector) -> RawSourceRecord:
    pages = [page async for page in connector.fetch(_window())]
    assert pages[0].next_cursor is not None
    assert pages[0].next_cursor["window_end"] == NOW.isoformat()
    return pages[0].records[0]


async def _records(connector: FetchingConnector) -> tuple[RawSourceRecord, ...]:
    pages = [page async for page in connector.fetch(_window())]
    return pages[0].records


def test_openreview_normalizes_wrapped_fields_decision_and_explicit_links() -> None:
    http = FixtureHttp(
        {"api2.openreview.net": ("application/json", (FIXTURES / "openreview.json").read_bytes())}
    )
    connector = OpenReviewConnector(http, ("ICLR.cc/2026/Conference",), clock=lambda: NOW)
    raw = asyncio.run(_one(connector))
    normalized = connector.normalize(raw)

    assert connector.contract_version == "1.0"
    assert connector.trust_tier is TrustTier.A
    assert normalized.publication_status is PublicationStatus.ACCEPTED
    assert [author.display_name for author in normalized.authors] == ["Ada Lovelace", "Alan Turing"]
    assert [identity.normalized_value for identity in normalized.identities] == [
        "forum-1",
        "2607.00001",
    ]
    assert normalized.repository_urls == ("https://github.com/example/agent-memory",)
    assert connector.validate(normalized) == []
    assert "content.venueid=ICLR.cc%2F2026%2FConference" in http.requests[0][1]


def test_openreview_never_exposes_blind_authors_or_infers_acceptance() -> None:
    http = FixtureHttp(
        {
            "api2.openreview.net": (
                "application/json",
                (FIXTURES / "openreview_blind.json").read_bytes(),
            )
        }
    )
    connector = OpenReviewConnector(http, ("ICLR.cc/2026/Conference",), clock=lambda: NOW)
    records = asyncio.run(_records(connector))
    normalized = connector.normalize(records[0])
    assert normalized.authors == ()
    assert normalized.publication_status is PublicationStatus.SUBMITTED
    assert normalized.extra["blind"] is True


def test_github_enrichment_is_objective_canonical_and_never_executes_code() -> None:
    http = FixtureHttp(
        {
            "/releases": (
                "application/json",
                (FIXTURES / "github_release.json").read_bytes(),
            ),
            "api.github.com": ("application/json", (FIXTURES / "github.json").read_bytes()),
        }
    )
    connector = GitHubConnector(
        http,
        ("https://github.com/example/agent-memory.git?utm_source=test",),
        clock=lambda: NOW,
    )
    records = asyncio.run(_records(connector))
    normalized = connector.normalize(records[0])
    assert normalized.canonical_url == "https://github.com/example/agent-memory"
    assert normalized.extra == {
        "archived": False,
        "fork": False,
        "default_branch": "main",
        "pushed_at": "2026-07-21T02:00:00Z",
        "open_issues_count": 3,
        "has_downloads": True,
        "license": "Apache-2.0",
    }
    assert normalized.license_hint == "Apache-2.0"
    release = connector.normalize(records[1])
    assert release.work_type.value == "release"
    assert release.extra["tag"] == "v1.2.0"
    assert release.repository_urls == ("https://github.com/example/agent-memory",)


def test_huggingface_discovers_model_dataset_and_space_contract_with_arxiv_link() -> None:
    payload = (FIXTURES / "huggingface.json").read_bytes()
    http = FixtureHttp(
        {
            "/api/models": ("application/json", payload),
            "/api/datasets": ("application/json", b"[]"),
            "/api/spaces": ("application/json", b"[]"),
        }
    )
    connector = HuggingFaceConnector(http, clock=lambda: NOW)
    normalized = connector.normalize(asyncio.run(_one(connector)))
    assert normalized.canonical_url == "https://huggingface.co/example/agent-memory-4b"
    assert [identity.normalized_value for identity in normalized.identities] == [
        "models:example/agent-memory-4b",
        "2607.00001",
    ]
    assert len(http.requests) == 3


def test_official_rss_extracts_only_allowlisted_feed_links_and_explicit_identities() -> None:
    http = FixtureHttp(
        {"feed.xml": ("application/rss+xml", (FIXTURES / "official.xml").read_bytes())}
    )
    connector = RssAtomConnector(http, ("https://huggingface.co/blog/feed.xml",), clock=lambda: NOW)
    normalized = connector.normalize(asyncio.run(_one(connector)))
    assert normalized.title == "Agent Memory released"
    assert normalized.extra["arxiv_ids"] == ["2607.00001"]
    assert normalized.repository_urls == ("https://github.com/example/agent-memory",)
    assert connector.validate(normalized) == []


def test_connector_fixtures_contain_no_credentials() -> None:
    text = " ".join(path.read_text(encoding="utf-8") for path in FIXTURES.iterdir()).casefold()
    assert "authorization" not in text
    assert "github_token" not in text
    assert "bearer " not in text
    assert json.loads((FIXTURES / "openreview.json").read_text())["notes"][0]["forum"] == "forum-1"


def test_paper_repository_model_and_announcement_become_one_linked_event() -> None:
    root = Path(f"data/.test-multisource/{uuid4().hex}")
    settings = AppSettings(paths=PathSettings(data_root=root, database_path=Path("state/test.db")))
    initialize_directories(settings.paths)
    database = SQLiteDatabase(settings.paths.database_path)
    MigrationRunner(database).migrate()
    connection = database.connect()
    try:
        now = NOW.isoformat().replace("+00:00", "Z")
        with transaction(connection):
            connection.execute(
                """INSERT INTO works(id,work_type,canonical_title,normalized_title,language,
                publication_status,lifecycle_state,created_at,updated_at)
                VALUES('paper-1','paper','Agent Memory','agent memory','en','preprint',
                'normalized',?,?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO work_versions(id,work_id,version_label,title,metadata_json,
                observed_at,is_current) VALUES('version-1','paper-1','v1','Agent Memory',?, ?,1)""",
                (
                    json.dumps({"repository_urls": ["https://github.com/example/agent-memory"]}),
                    now,
                ),
            )
            connection.execute("UPDATE works SET current_version_id='version-1' WHERE id='paper-1'")
            connection.execute(
                """INSERT INTO external_ids(
                id,work_id,id_type,normalized_value,raw_value,created_at)
                VALUES('arxiv-id','paper-1','arxiv','2607.00001','2607.00001',?)""",
                (now,),
            )
            service = MultiSourceDiscoveryService(
                settings,
                connection,
                SQLiteRepositories.for_connection(connection),
                clock=lambda: NOW,
            )
            normalized_records = (
                GitHubConnector(
                    FixtureHttp(
                        {
                            "/releases": (
                                "application/json",
                                (FIXTURES / "github_release.json").read_bytes(),
                            ),
                            "api.github.com": (
                                "application/json",
                                (FIXTURES / "github.json").read_bytes(),
                            ),
                        }
                    ),
                    ("https://github.com/example/agent-memory",),
                    clock=lambda: NOW,
                ),
                HuggingFaceConnector(
                    FixtureHttp(
                        {
                            "/api/models": (
                                "application/json",
                                (FIXTURES / "huggingface.json").read_bytes(),
                            ),
                            "/api/datasets": ("application/json", b"[]"),
                            "/api/spaces": ("application/json", b"[]"),
                        }
                    ),
                    clock=lambda: NOW,
                ),
                RssAtomConnector(
                    FixtureHttp(
                        {
                            "feed.xml": (
                                "application/rss+xml",
                                (FIXTURES / "official.xml").read_bytes(),
                            )
                        }
                    ),
                    ("https://huggingface.co/blog/feed.xml",),
                    clock=lambda: NOW,
                ),
            )
            for index, connector in enumerate(normalized_records, 1):
                raw = asyncio.run(_one(connector))
                source_key = connector.key
                connection.execute(
                    """INSERT INTO sources(id,source_key,display_name,trust_tier,base_url,
                    enabled,poll_interval_minutes,minimum_request_interval_ms,config_json,
                    cursor_json,connector_version,health_status,created_at,updated_at)
                    VALUES(?,?,?,'A','https://example.test',1,60,0,'{}',NULL,?,'healthy',?,?)""",
                    (
                        f"source-{index}",
                        source_key,
                        source_key,
                        connector.connector_version,
                        now,
                        now,
                    ),
                )
                record = SourceRecord(
                    id=f"record-{index}",
                    source_id=f"source-{index}",
                    upstream_id=raw.upstream_id,
                    upstream_version=raw.upstream_version,
                    canonical_url=raw.canonical_url,
                    payload_sha256=f"hash-{index}",
                    raw_payload_path=f"raw/{index}",
                    observed_at=NOW,
                    published_at=raw.published_at,
                    updated_at_upstream=raw.updated_at,
                    normalization_status=NormalizationStatus.NORMALIZED,
                )
                connection.execute(
                    """INSERT INTO source_records(id,source_id,upstream_id,upstream_version,
                    canonical_url,payload_sha256,raw_payload_path,observed_at,published_at,
                    updated_at_upstream,normalization_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.id,
                        record.source_id,
                        record.upstream_id,
                        record.upstream_version,
                        record.canonical_url,
                        record.payload_sha256,
                        record.raw_payload_path,
                        now,
                        None if record.published_at is None else record.published_at.isoformat(),
                        None
                        if record.updated_at_upstream is None
                        else record.updated_at_upstream.isoformat(),
                        record.normalization_status.value,
                    ),
                )
                assert (
                    service.persist_normalized_artifact(connector.normalize(raw), record)
                    == "paper-1"
                )

        page = LinkedEventReader(connection).list()
        assert page.total == 1
        assert page.items[0].primary_work_id == "paper-1"
        assert {source.source_key for source in page.items[0].sources} == {
            "github",
            "huggingface",
            "official-rss",
        }
        assert page.items[0].corroboration == 1.0
    finally:
        connection.close()
        shutil.rmtree(settings.paths.data_root, ignore_errors=True)
