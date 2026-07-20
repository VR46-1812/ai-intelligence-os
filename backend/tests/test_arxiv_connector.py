"""Deterministic Atom parsing and checkpoint tests for the M2.2 arXiv connector."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.models import ExternalIdType, PublicationStatus
from app.ingestion.contracts import ConnectorException, ConnectorPage, FetchWindow, HttpResponse
from app.sources.arxiv import ARXIV_MINIMUM_REQUEST_INTERVAL_MS, ArxivConnector

FIXTURES = Path(__file__).parent / "fixtures" / "arxiv"
SINCE = datetime(2026, 7, 17, tzinfo=UTC)
UNTIL = datetime(2026, 7, 21, tzinfo=UTC)


class FixtureHttpClient:
    def __init__(self, payloads: tuple[bytes, ...]) -> None:
        self._payloads = iter(payloads)
        self.calls: list[tuple[str, int]] = []

    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: tuple[str, ...],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        del expected_media_types, headers
        assert source_key == "arxiv"
        self.calls.append((url, minimum_request_interval_ms))
        return HttpResponse(
            status_code=200,
            media_type="application/atom+xml",
            content=next(self._payloads),
            response_metadata={"request_url": url},
        )


async def _pages(connector: ArxivConnector, window: FetchWindow) -> tuple[ConnectorPage, ...]:
    return tuple([page async for page in connector.fetch(window)])


def test_fetch_uses_only_configured_categories_and_resumes_from_checkpoint() -> None:
    http = FixtureHttpClient(((FIXTURES / "page.xml").read_bytes(),))
    connector = ArxivConnector(http, ("cs.AI", "stat.ML"), maximum_pages_per_run=1)
    pages = asyncio.run(
        _pages(
            connector,
            FetchWindow(
                since=SINCE,
                until=UNTIL,
                cursor={"position": "10", "window_end": UNTIL.isoformat()},
                page_size=4,
            ),
        )
    )

    assert len(pages) == 1
    assert len(pages[0].records) == 4
    assert pages[0].next_cursor is not None
    assert pages[0].next_cursor["position"] == "14"
    query = parse_qs(urlparse(http.calls[0][0]).query)
    assert query["start"] == ["10"]
    assert query["search_query"] == ["(cat:cs.AI OR cat:stat.ML)"]
    assert "cs.CL" not in http.calls[0][0]
    assert http.calls[0][1] == ARXIV_MINIMUM_REQUEST_INTERVAL_MS
    assert [entry.arxiv_id for entry in connector.fetched_entries] == [
        "2607.01234",
        "2607.01234",
        "2607.05678",
    ]


def test_normalize_preserves_identity_revision_authors_dates_categories_and_provenance() -> None:
    http = FixtureHttpClient(((FIXTURES / "page.xml").read_bytes(),))
    connector = ArxivConnector(http, ("cs.AI", "cs.LG", "cs.CL"), maximum_pages_per_run=1)
    page = asyncio.run(_pages(connector, FetchWindow(since=SINCE, until=UNTIL, page_size=5)))[0]

    revision = connector.normalize(page.records[0])
    assert revision.upstream_id == "2607.01234"
    assert revision.upstream_version == "v2"
    assert revision.canonical_url == "https://arxiv.org/abs/2607.01234"
    assert revision.title == "Reliable Local Agents, Revised"
    assert revision.abstract == "Revision with deterministic evidence handling."
    assert revision.publication_status is PublicationStatus.PREPRINT
    assert revision.published_at == datetime(2026, 7, 18, 9, tzinfo=UTC)
    assert revision.updated_at == datetime(2026, 7, 20, 10, tzinfo=UTC)
    assert [author.display_name for author in revision.authors] == [
        "Ada Lovelace",
        "Grace Hopper",
    ]
    assert revision.source_topics == ("cs.AI", "cs.LG")
    assert revision.document_urls == ("https://arxiv.org/pdf/2607.01234v2",)
    identities = {identity.id_type: identity.normalized_value for identity in revision.identities}
    assert identities == {
        ExternalIdType.ARXIV: "2607.01234",
        ExternalIdType.DOI: "10.1234/local.2",
    }
    assert page.records[0].response_metadata["request_url"]

    partial = connector.normalize(page.records[2])
    assert partial.abstract is None
    assert partial.document_urls == ()
    assert partial.source_topics == ("cs.CL",)
    assert connector.validate(partial) == []


def test_malformed_entry_is_captured_and_isolated_during_normalization() -> None:
    connector = ArxivConnector(
        FixtureHttpClient(((FIXTURES / "page.xml").read_bytes(),)),
        ("cs.AI",),
        maximum_pages_per_run=1,
    )
    page = asyncio.run(_pages(connector, FetchWindow(since=SINCE, until=UNTIL, page_size=5)))[0]

    malformed = page.records[3]
    assert malformed.upstream_id == "2607.09999"
    with pytest.raises(ConnectorException, match="NORMALIZATION_FAILED"):
        connector.normalize(malformed)


def test_fetch_stops_at_window_boundary_without_advancing_past_unconsumed_entries() -> None:
    connector = ArxivConnector(
        FixtureHttpClient(((FIXTURES / "older-page.xml").read_bytes(),)),
        ("cs.AI",),
    )
    page = asyncio.run(_pages(connector, FetchWindow(since=SINCE, until=UNTIL, page_size=2)))[0]

    assert page.exhausted is True
    assert page.records == ()
    assert page.next_cursor is not None and page.next_cursor["position"] == "0"


def test_fetch_paginates_and_stale_window_checkpoint_restarts_at_zero() -> None:
    first_payload = (
        (FIXTURES / "page.xml")
        .read_bytes()
        .replace(
            b"<opensearch:totalResults>4</opensearch:totalResults>",
            b"<opensearch:totalResults>6</opensearch:totalResults>",
        )
    )
    http = FixtureHttpClient((first_payload, (FIXTURES / "older-page.xml").read_bytes()))
    connector = ArxivConnector(http, ("cs.AI",))
    pages = asyncio.run(
        _pages(
            connector,
            FetchWindow(
                since=SINCE,
                until=UNTIL,
                cursor={"position": "99", "window_end": SINCE.isoformat()},
                page_size=4,
            ),
        )
    )

    assert len(pages) == 2
    assert pages[0].next_cursor is not None and pages[0].next_cursor["position"] == "4"
    assert pages[1].next_cursor is not None and pages[1].next_cursor["position"] == "4"
    assert parse_qs(urlparse(http.calls[0][0]).query)["start"] == ["0"]
    assert parse_qs(urlparse(http.calls[1][0]).query)["start"] == ["4"]
