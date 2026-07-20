"""Official arXiv Atom connector and deterministic metadata normalization."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.catalog.identity import (
    CatalogIdentityError,
    IdentityInput,
    normalize_author,
    normalize_identifier,
    normalize_title,
)
from app.config import ArxivCategory
from app.domain.models import ExternalIdType, JsonObject, PublicationStatus, TrustTier, WorkType
from app.ingestion.contracts import (
    ConnectorErrorCode,
    ConnectorException,
    ConnectorFailure,
    ConnectorPage,
    FetchWindow,
    HttpResponse,
    NormalizedAuthor,
    NormalizedIdentity,
    NormalizedRecord,
    RawSourceRecord,
)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_CANONICAL_ROOT = "https://arxiv.org/abs/"
ARXIV_MINIMUM_REQUEST_INTERVAL_MS = 3000
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
_WHITESPACE = re.compile(r"\s+")


class ArxivHttpClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: tuple[str, ...],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...


class ArxivConnector:
    key = "arxiv"
    trust_tier = TrustTier.A
    connector_version = "arxiv-v1"

    def __init__(
        self,
        http: ArxivHttpClient,
        categories: tuple[ArxivCategory, ...],
        *,
        minimum_request_interval_ms: int = ARXIV_MINIMUM_REQUEST_INTERVAL_MS,
        maximum_pages_per_run: int | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not categories or len(categories) != len(set(categories)):
            raise ValueError("arXiv categories must be non-empty and unique")
        if maximum_pages_per_run is not None and not 1 <= maximum_pages_per_run <= 100:
            raise ValueError("maximum_pages_per_run must be between 1 and 100")
        self.categories = categories
        self.minimum_request_interval_ms = max(
            ARXIV_MINIMUM_REQUEST_INTERVAL_MS, minimum_request_interval_ms
        )
        self.maximum_pages_per_run = maximum_pages_per_run
        self._http = http
        self._clock = clock

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        start = 0 if window.cursor is None else self._cursor_position(window.cursor, window.until)
        pages = 0
        while True:
            response = await self._http.get(
                self._query_url(window, start),
                source_key=self.key,
                minimum_request_interval_ms=self.minimum_request_interval_ms,
                expected_media_types=("application/atom+xml", "application/xml", "text/xml"),
                headers={"Accept": "application/atom+xml, application/xml;q=0.9"},
            )
            root = self._parse_feed(response.content)
            entries = tuple(root.findall(f"{ATOM}entry"))
            total_results = self._total_results(root, start + len(entries))
            records: list[RawSourceRecord] = []
            reached_window_start = False
            for entry in entries:
                updated_at = self._optional_datetime(entry.findtext(f"{ATOM}updated"))
                if updated_at is not None and updated_at < window.since:
                    reached_window_start = True
                    continue
                if updated_at is None or updated_at <= window.until:
                    records.append(self._raw_record(entry, response, start))
            next_position = start + len(entries)
            pages += 1
            capped = self.maximum_pages_per_run is not None and pages >= self.maximum_pages_per_run
            exhausted = (
                capped
                or reached_window_start
                or not entries
                or len(entries) < window.page_size
                or next_position >= total_results
            )
            yield ConnectorPage(
                records=tuple(records),
                next_cursor={
                    "schema_version": 1,
                    "position": str(next_position),
                    "window_end": window.until.isoformat(),
                    "last_upstream_id": None if not records else records[-1].upstream_id,
                },
                exhausted=exhausted,
            )
            if exhausted:
                return
            start = next_position

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            entry = ET.fromstring(record.payload)
            raw_identity = self._required_text(entry, f"{ATOM}id", "arXiv id")
            arxiv_identity = normalize_identifier(
                IdentityInput(id_type=ExternalIdType.ARXIV, raw_value=raw_identity)
            )
            title = self._required_text(entry, f"{ATOM}title", "title")
            published = self._required_datetime(entry, f"{ATOM}published", "published date")
            updated = self._optional_datetime(entry.findtext(f"{ATOM}updated"))
            authors = tuple(
                NormalizedAuthor(
                    display_name=name,
                    normalized_name=normalize_author(name),
                    order=index,
                )
                for index, author in enumerate(entry.findall(f"{ATOM}author"), start=1)
                if (name := self._clean(author.findtext(f"{ATOM}name")))
            )
            identities = [
                NormalizedIdentity(
                    id_type=ExternalIdType.ARXIV,
                    raw_value=raw_identity,
                    normalized_value=arxiv_identity.normalized_value,
                )
            ]
            raw_doi = self._clean(entry.findtext(f"{ARXIV}doi"))
            if raw_doi is not None:
                try:
                    doi = normalize_identifier(
                        IdentityInput(id_type=ExternalIdType.DOI, raw_value=raw_doi)
                    )
                except CatalogIdentityError:
                    doi = None
                if doi is not None:
                    identities.append(
                        NormalizedIdentity(
                            id_type=ExternalIdType.DOI,
                            raw_value=raw_doi,
                            normalized_value=doi.normalized_value,
                        )
                    )
            categories = tuple(
                dict.fromkeys(
                    term
                    for category in entry.findall(f"{ATOM}category")
                    if (term := self._clean(category.get("term"))) is not None
                )
            )
            pdf_urls = tuple(
                href
                for link in entry.findall(f"{ATOM}link")
                if (href := self._clean(link.get("href"))) is not None
                and (link.get("title") == "pdf" or link.get("type") == "application/pdf")
            )
            stable_id = arxiv_identity.normalized_value
            version = arxiv_identity.version_label or record.upstream_version or "v1"
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=stable_id,
                upstream_version=version,
                work_type=WorkType.PAPER,
                title=title,
                normalized_title=normalize_title(title),
                abstract=self._clean(entry.findtext(f"{ATOM}summary")),
                canonical_url=f"{ARXIV_CANONICAL_ROOT}{stable_id}",
                publication_status=PublicationStatus.PREPRINT,
                published_at=published,
                updated_at=updated,
                identities=tuple(identities),
                authors=authors,
                source_topics=categories,
                document_urls=pdf_urls,
                repository_urls=(),
                license_hint=self._clean(entry.findtext(f"{ARXIV}license")),
                extra={
                    "primary_category": self._primary_category(entry),
                    "comment": self._clean(entry.findtext(f"{ARXIV}comment")),
                    "journal_reference": self._clean(entry.findtext(f"{ARXIV}journal_ref")),
                },
            )
        except (ET.ParseError, CatalogIdentityError, ValueError) as error:
            raise self._normalization_failure("arXiv entry could not be normalized") from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        errors: list[str] = []
        if record.source_key != self.key:
            errors.append("source_key must be arxiv")
        if not record.authors:
            errors.append("at least one author is required")
        if not any(identity.id_type is ExternalIdType.ARXIV for identity in record.identities):
            errors.append("a normalized arXiv identity is required")
        if record.publication_status is not PublicationStatus.PREPRINT:
            errors.append("arXiv metadata must default to preprint")
        return errors

    def _query_url(self, window: FetchWindow, start: int) -> str:
        category_query = " OR ".join(f"cat:{category}" for category in self.categories)
        return str(
            httpx.URL(
                ARXIV_API_URL,
                params={
                    "search_query": f"({category_query})",
                    "start": str(start),
                    "max_results": str(window.page_size),
                    "sortBy": "lastUpdatedDate",
                    "sortOrder": "descending",
                },
            )
        )

    def _raw_record(
        self, entry: ET.Element, response: HttpResponse, page_start: int
    ) -> RawSourceRecord:
        payload = ET.tostring(entry, encoding="utf-8", xml_declaration=True)
        raw_identity = self._clean(entry.findtext(f"{ATOM}id"))
        parse_warning: str | None = None
        try:
            if raw_identity is None:
                raise CatalogIdentityError("missing arXiv id")
            identity = normalize_identifier(
                IdentityInput(id_type=ExternalIdType.ARXIV, raw_value=raw_identity)
            )
            stable_id = identity.normalized_value
            version = identity.version_label or "v1"
            canonical_url = f"{ARXIV_CANONICAL_ROOT}{stable_id}"
        except CatalogIdentityError:
            digest = hashlib.sha256(payload).hexdigest()[:16]
            stable_id = f"malformed:{digest}"
            version = None
            canonical_url = "https://arxiv.org/"
            parse_warning = "missing_or_invalid_arxiv_identity"
        metadata = {
            **response.response_metadata,
            "http_status": response.status_code,
            "page_start": page_start,
            "parse_warning": parse_warning,
        }
        return RawSourceRecord(
            source_key=self.key,
            upstream_id=stable_id,
            upstream_version=version,
            canonical_url=canonical_url,
            observed_at=self._clock(),
            published_at=self._optional_datetime(entry.findtext(f"{ATOM}published")),
            updated_at=self._optional_datetime(entry.findtext(f"{ATOM}updated")),
            media_type="application/atom+xml",
            payload=payload,
            response_metadata={key: value for key, value in metadata.items() if value is not None},
        )

    @staticmethod
    def _parse_feed(content: bytes) -> ET.Element:
        try:
            return ET.fromstring(content)
        except ET.ParseError as error:
            raise ConnectorException(
                ConnectorFailure(
                    code=ConnectorErrorCode.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="arXiv returned malformed Atom XML",
                    attempts=1,
                )
            ) from error

    @staticmethod
    def _total_results(root: ET.Element, fallback: int) -> int:
        value = root.findtext(f"{OPENSEARCH}totalResults")
        try:
            return max(0, int(value)) if value is not None else fallback
        except ValueError:
            return fallback

    @staticmethod
    def _cursor_position(cursor: JsonObject, window_end: datetime) -> int:
        cursor_window = ArxivConnector._optional_datetime(
            None if "window_end" not in cursor else str(cursor["window_end"])
        )
        if cursor_window != window_end:
            return 0
        try:
            position = int(str(cursor["position"]))
        except (KeyError, ValueError) as error:
            raise ConnectorException(
                ConnectorFailure(
                    code=ConnectorErrorCode.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="arXiv checkpoint position is invalid",
                    attempts=1,
                )
            ) from error
        if position < 0:
            raise ConnectorException(
                ConnectorFailure(
                    code=ConnectorErrorCode.INVALID_RESPONSE,
                    retryable=False,
                    safe_message="arXiv checkpoint position cannot be negative",
                    attempts=1,
                )
            )
        return position

    @staticmethod
    def _clean(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _WHITESPACE.sub(" ", value).strip()
        return cleaned or None

    @classmethod
    def _required_text(cls, entry: ET.Element, path: str, field: str) -> str:
        value = cls._clean(entry.findtext(path))
        if value is None:
            raise ValueError(f"missing required {field}")
        return value

    @classmethod
    def _required_datetime(cls, entry: ET.Element, path: str, field: str) -> datetime:
        value = cls._optional_datetime(entry.findtext(path))
        if value is None:
            raise ValueError(f"missing or invalid required {field}")
        return value

    @staticmethod
    def _optional_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)

    @staticmethod
    def _primary_category(entry: ET.Element) -> str | None:
        primary = entry.find(f"{ARXIV}primary_category")
        return None if primary is None else ArxivConnector._clean(primary.get("term"))

    @staticmethod
    def _normalization_failure(message: str) -> ConnectorException:
        return ConnectorException(
            ConnectorFailure(
                code=ConnectorErrorCode.NORMALIZATION_FAILED,
                retryable=False,
                safe_message=message,
                attempts=1,
            )
        )
