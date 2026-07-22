"""Versioned, fixture-testable connectors for V1.1 authoritative public sources."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, cast
from urllib.parse import quote, urlencode, urlparse

from app.catalog.identity import normalize_author, normalize_title
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

_SPACE = re.compile(r"\s+")
_ARXIV = re.compile(
    r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)([a-z.-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?", re.I
)
_GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
_TRANSCRIPT = re.compile(r"https://[^\s\"'<>]+\.(?:vtt|srt)(?:\?[^\s\"'<>]*)?", re.I)


class SourceHttpClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: tuple[str, ...],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned or None


def _datetime(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = _clean(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _wrapped(content: object, key: str) -> object:
    if not isinstance(content, dict):
        return None
    mapping = cast(dict[str, object], content)
    value = mapping.get(key)
    if isinstance(value, dict) and "value" in value:
        return cast(dict[str, object], value)["value"]
    return cast(object, value)


def _json_object(payload: bytes) -> dict[str, object]:
    value = cast(object, json.loads(payload))
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    return cast(dict[str, object], value)


def _json_list(payload: bytes) -> list[object]:
    value = cast(object, json.loads(payload))
    if not isinstance(value, list):
        raise ValueError("JSON value must be a list")
    return cast(list[object], value)


def _json_value(payload: bytes) -> object:
    return cast(object, json.loads(payload))


def _object_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _failure(source: str, message: str) -> ConnectorException:
    return ConnectorException(
        ConnectorFailure(
            code=ConnectorErrorCode.NORMALIZATION_FAILED,
            retryable=False,
            safe_message=f"{source} record could not be normalized: {message}",
            attempts=1,
        )
    )


def _checkpoint(position: int, window: FetchWindow, upstream_id: str | None) -> JsonObject:
    return {
        "schema_version": 1,
        "position": str(position),
        "window_end": window.until.isoformat(),
        "last_upstream_id": upstream_id,
    }


class OpenReviewConnector:
    contract_version: str = "1.0"
    key = "openreview"
    trust_tier = TrustTier.A
    connector_version = "openreview-v1"

    def __init__(
        self,
        http: SourceHttpClient,
        venues: tuple[str, ...],
        *,
        minimum_request_interval_ms: int = 1000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not venues:
            raise ValueError("OpenReview venue allowlist must not be empty")
        self._http = http
        self.venues = venues
        self.minimum_request_interval_ms = minimum_request_interval_ms
        self._clock = clock

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        offset = 0 if window.cursor is None else int(str(window.cursor.get("position", "0")))
        venue = self.venues[0]
        query = urlencode({"content.venueid": venue, "limit": window.page_size, "offset": offset})
        response = await self._http.get(
            f"https://api2.openreview.net/notes?{query}",
            source_key=self.key,
            minimum_request_interval_ms=self.minimum_request_interval_ms,
            expected_media_types=("application/json",),
            headers={"Accept": "application/json"},
        )
        try:
            payload = _json_object(response.content)
            notes = payload.get("notes", [])
            if not isinstance(notes, list):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError) as error:
            raise _failure("OpenReview", "invalid JSON response") from error
        records = tuple(
            self._raw(cast(dict[str, object], note), response)
            for note in cast(list[object], notes)
            if isinstance(note, dict)
        )
        position = offset + len(records)
        yield ConnectorPage(
            records=records,
            next_cursor=_checkpoint(position, window, records[-1].upstream_id if records else None),
            exhausted=True,
        )

    def _raw(self, note: dict[str, object], response: HttpResponse) -> RawSourceRecord:
        note_id = _clean(note.get("id"))
        forum = _clean(note.get("forum")) or note_id
        if note_id is None or forum is None:
            raise _failure("OpenReview", "missing note identity")
        content = note.get("content")
        title = _clean(_wrapped(content, "title")) or "Untitled OpenReview submission"
        modified = _datetime(note.get("mdate")) or self._clock()
        raw = json.dumps(note, ensure_ascii=False, separators=(",", ":")).encode()
        return RawSourceRecord(
            source_key=self.key,
            upstream_id=forum,
            upstream_version=str(note.get("version") or int(modified.timestamp())),
            canonical_url=f"https://openreview.net/forum?id={quote(forum, safe='_-')}",
            observed_at=self._clock(),
            published_at=_datetime(note.get("cdate")),
            updated_at=modified,
            media_type="application/json",
            payload=raw,
            response_metadata={**response.response_metadata, "title": title},
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            note = _json_object(record.payload)
            content = note.get("content", {})
            title = _clean(_wrapped(content, "title"))
            if title is None:
                raise ValueError("missing title")
            abstract = _clean(_wrapped(content, "abstract"))
            raw_authors = _wrapped(content, "authors")
            blind = bool(note.get("nonreaders")) or not isinstance(raw_authors, list)
            author_values = cast(list[object], raw_authors) if isinstance(raw_authors, list) else []
            authors = (
                ()
                if blind
                else tuple(
                    NormalizedAuthor(
                        display_name=name,
                        normalized_name=normalize_author(name),
                        order=index,
                    )
                    for index, raw_name in enumerate(author_values, 1)
                    if (name := _clean(raw_name)) is not None and "anonymous" not in name.casefold()
                )
            )
            venue = _clean(_wrapped(content, "venue")) or _clean(_wrapped(content, "venueid"))
            decision = _clean(_wrapped(content, "decision"))
            status = PublicationStatus.SUBMITTED
            decision_text = f"{venue or ''} {decision or ''}".casefold()
            if "accept" in decision_text:
                status = PublicationStatus.ACCEPTED
            elif "reject" in decision_text:
                status = PublicationStatus.SUBMITTED
            text = " ".join(filter(None, (title, abstract)))
            identities = [
                NormalizedIdentity(
                    id_type=ExternalIdType.OPENREVIEW,
                    raw_value=record.upstream_id,
                    normalized_value=record.upstream_id,
                )
            ]
            arxiv_ids = tuple(
                dict.fromkeys(match.group(1).casefold() for match in _ARXIV.finditer(text))
            )
            identities.extend(
                NormalizedIdentity(
                    id_type=ExternalIdType.ARXIV, raw_value=value, normalized_value=value
                )
                for value in arxiv_ids
            )
            repositories = tuple(
                dict.fromkeys(
                    f"https://github.com/{m.group(1)}/{m.group(2).removesuffix('.git')}"
                    for m in _GITHUB.finditer(text)
                )
            )
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=record.upstream_id,
                upstream_version=record.upstream_version,
                work_type=WorkType.PAPER,
                title=title,
                normalized_title=normalize_title(title),
                abstract=abstract,
                canonical_url=record.canonical_url,
                publication_status=status,
                published_at=record.published_at,
                updated_at=record.updated_at,
                identities=tuple(identities),
                authors=authors,
                source_topics=(venue,) if venue else (),
                document_urls=(),
                repository_urls=repositories,
                extra={
                    "venue": venue,
                    "decision": decision,
                    "blind": blind,
                    "arxiv_ids": list(arxiv_ids),
                },
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError) as error:
            raise _failure("OpenReview", str(error)) from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        errors: list[str] = []
        if record.source_key != self.key:
            errors.append("source_key must be openreview")
        if not record.canonical_url.startswith("https://openreview.net/forum?id="):
            errors.append("canonical URL must use OpenReview")
        if record.publication_status is PublicationStatus.ACCEPTED and not record.extra.get(
            "decision"
        ):
            errors.append("accepted status requires public decision evidence")
        return errors


class GitHubConnector:
    contract_version: str = "1.0"
    key = "github"
    trust_tier = TrustTier.A
    connector_version = "github-v1"

    def __init__(
        self,
        http: SourceHttpClient,
        repository_urls: tuple[str, ...],
        *,
        search_queries: tuple[str, ...] = (),
        token: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self.repositories = tuple(
            dict.fromkeys(self.canonical_repository(url) for url in repository_urls)
        )
        self._token = token
        self.search_queries = tuple(
            dict.fromkeys(query.strip() for query in search_queries if query.strip())
        )
        self._clock = clock

    @staticmethod
    def canonical_repository(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() != "github.com":
            raise ValueError("GitHub repository URL must use https://github.com")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("GitHub repository URL must include owner and repository")
        return f"https://github.com/{parts[0]}/{parts[1].removesuffix('.git')}"

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        records: list[RawSourceRecord] = []
        for search_query in self.search_queries:
            if len(records) >= window.page_size:
                break
            query = urlencode(
                {
                    "q": search_query,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": window.page_size - len(records),
                }
            )
            response = await self._http.get(
                f"https://api.github.com/search/repositories?{query}",
                source_key=self.key,
                minimum_request_interval_ms=1000,
                expected_media_types=("application/json",),
                headers=headers,
            )
            search = _json_object(response.content)
            for value in _object_list(search.get("items", [])):
                if len(records) >= window.page_size or not isinstance(value, dict):
                    break
                data = cast(dict[str, object], value)
                full_name = _clean(data.get("full_name"))
                if full_name is None:
                    continue
                records.append(
                    RawSourceRecord(
                        source_key=self.key,
                        upstream_id=full_name.casefold(),
                        upstream_version=_clean(data.get("pushed_at")),
                        canonical_url=f"https://github.com/{full_name}",
                        observed_at=self._clock(),
                        published_at=_datetime(data.get("created_at")),
                        updated_at=_datetime(data.get("updated_at")),
                        media_type="application/json",
                        payload=json.dumps(
                            data, ensure_ascii=False, separators=(",", ":")
                        ).encode(),
                        response_metadata={
                            **response.response_metadata,
                            "discovery": "configured_search",
                        },
                    )
                )
        for repository in self.repositories:
            if len(records) >= window.page_size:
                break
            owner, name = urlparse(repository).path.strip("/").split("/", 1)
            response = await self._http.get(
                f"https://api.github.com/repos/{quote(owner)}/{quote(name)}",
                source_key=self.key,
                minimum_request_interval_ms=250,
                expected_media_types=("application/json",),
                headers=headers,
            )
            data = _json_object(response.content)
            full_name = _clean(data.get("full_name"))
            if full_name is None:
                raise _failure("GitHub", "missing repository identity")
            records.append(
                RawSourceRecord(
                    source_key=self.key,
                    upstream_id=full_name.casefold(),
                    upstream_version=_clean(data.get("pushed_at")),
                    canonical_url=f"https://github.com/{full_name}",
                    observed_at=self._clock(),
                    published_at=_datetime(data.get("created_at")),
                    updated_at=_datetime(data.get("updated_at")),
                    media_type="application/json",
                    payload=response.content,
                    response_metadata=response.response_metadata,
                )
            )
            if len(records) >= window.page_size:
                continue
            releases_response = await self._http.get(
                f"https://api.github.com/repos/{quote(owner)}/{quote(name)}/releases?per_page=1",
                source_key=self.key,
                minimum_request_interval_ms=250,
                expected_media_types=("application/json",),
                headers=headers,
            )
            releases = _json_list(releases_response.content)
            latest = (
                cast(dict[str, object], releases[0])
                if releases and isinstance(releases[0], dict)
                else None
            )
            tag = None if latest is None else _clean(latest.get("tag_name"))
            release_url = None if latest is None else _clean(latest.get("html_url"))
            if latest is not None and tag is not None and release_url is not None:
                records.append(
                    RawSourceRecord(
                        source_key=self.key,
                        upstream_id=f"{full_name.casefold()}:release:{tag.casefold()}",
                        upstream_version=tag,
                        canonical_url=release_url,
                        observed_at=self._clock(),
                        published_at=_datetime(latest.get("published_at")),
                        updated_at=_datetime(latest.get("published_at")),
                        media_type="application/json",
                        payload=releases_response.content,
                        response_metadata=releases_response.response_metadata,
                    )
                )
        yield ConnectorPage(
            records=tuple(records),
            next_cursor=_checkpoint(
                len(records), window, records[-1].upstream_id if records else None
            ),
            exhausted=True,
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            value = _json_value(record.payload)
            if isinstance(value, list):
                release_values = cast(list[object], value)
                if not release_values or not isinstance(release_values[0], dict):
                    raise ValueError("missing GitHub release metadata")
                release = cast(dict[str, object], release_values[0])
                return self._normalize_release(record, release)
            if not isinstance(value, dict):
                raise ValueError("missing GitHub repository metadata")
            data = cast(dict[str, object], value)
            full_name = str(data["full_name"])
            title = full_name
            topics = tuple(
                item for item in _object_list(data.get("topics", [])) if isinstance(item, str)
            )
            license_data = data.get("license")
            license_hint = (
                _clean(cast(dict[str, object], license_data).get("spdx_id"))
                if isinstance(license_data, dict)
                else None
            )
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=full_name.casefold(),
                upstream_version=record.upstream_version,
                work_type=WorkType.REPOSITORY,
                title=title,
                normalized_title=normalize_title(title),
                abstract=_clean(data.get("description")),
                canonical_url=self.canonical_repository(record.canonical_url),
                publication_status=PublicationStatus.PUBLISHED,
                published_at=record.published_at,
                updated_at=record.updated_at,
                identities=(
                    NormalizedIdentity(
                        id_type=ExternalIdType.GITHUB,
                        raw_value=full_name,
                        normalized_value=full_name.casefold(),
                    ),
                ),
                authors=(),
                source_topics=topics,
                document_urls=(),
                repository_urls=(self.canonical_repository(record.canonical_url),),
                license_hint=license_hint,
                extra={
                    "archived": bool(data.get("archived")),
                    "fork": bool(data.get("fork")),
                    "default_branch": _clean(data.get("default_branch")),
                    "pushed_at": _clean(data.get("pushed_at")),
                    "open_issues_count": _integer(data.get("open_issues_count")),
                    "has_downloads": bool(data.get("has_downloads")),
                    "license": license_hint,
                },
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise _failure("GitHub", "invalid repository metadata") from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        return (
            []
            if record.canonical_url.startswith("https://github.com/")
            else ["invalid GitHub canonical URL"]
        )

    def _normalize_release(
        self, record: RawSourceRecord, release: dict[str, object]
    ) -> NormalizedRecord:
        tag = _clean(release.get("tag_name"))
        if tag is None:
            raise ValueError("GitHub release is missing its tag")
        path = urlparse(record.canonical_url).path.strip("/").split("/")
        if len(path) < 2:
            raise ValueError("GitHub release URL is invalid")
        repository_url = f"https://github.com/{path[0]}/{path[1]}"
        identity = f"{path[0]}/{path[1]}@{tag}".casefold()
        return NormalizedRecord(
            source_key=self.key,
            upstream_id=record.upstream_id,
            upstream_version=tag,
            work_type=WorkType.RELEASE,
            title=f"{path[0]}/{path[1]} {tag}",
            normalized_title=normalize_title(f"{path[0]} {path[1]} {tag}"),
            abstract=_clean(release.get("name")) or _clean(release.get("body")),
            canonical_url=record.canonical_url,
            publication_status=PublicationStatus.PUBLISHED,
            published_at=record.published_at,
            updated_at=record.updated_at,
            identities=(
                NormalizedIdentity(
                    id_type=ExternalIdType.GITHUB,
                    raw_value=identity,
                    normalized_value=identity,
                ),
            ),
            authors=(),
            source_topics=(),
            document_urls=(),
            repository_urls=(repository_url,),
            extra={"tag": tag, "prerelease": bool(release.get("prerelease"))},
        )


class HuggingFaceConnector:
    contract_version: str = "1.0"
    key = "huggingface"
    trust_tier = TrustTier.A
    connector_version = "huggingface-v1"
    _KINDS = (
        ("models", WorkType.MODEL),
        ("datasets", WorkType.DATASET),
        ("spaces", WorkType.OTHER),
    )

    def __init__(
        self, http: SourceHttpClient, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._http = http
        self._clock = clock

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        records: list[RawSourceRecord] = []
        per_kind = max(1, window.page_size // len(self._KINDS))
        for endpoint, _ in self._KINDS:
            filter_by = {
                "models": "text-generation",
                "datasets": "task_categories:text-generation",
                "spaces": "gradio",
            }[endpoint]
            query = urlencode(
                {
                    "filter": filter_by,
                    "sort": "lastModified",
                    "direction": "-1",
                    "limit": per_kind,
                    "full": "true",
                }
            )
            response = await self._http.get(
                f"https://huggingface.co/api/{endpoint}?{query}",
                source_key=self.key,
                minimum_request_interval_ms=250,
                expected_media_types=("application/json",),
                headers={"Accept": "application/json"},
            )
            payload = _json_list(response.content)
            for item in payload:
                if len(records) >= window.page_size or not isinstance(item, dict):
                    break
                item_map = cast(dict[str, object], item)
                item_id = _clean(item_map.get("id"))
                if item_id is None:
                    continue
                raw = json.dumps(
                    {"kind": endpoint, "item": item_map},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
                records.append(
                    RawSourceRecord(
                        source_key=self.key,
                        upstream_id=f"{endpoint}:{item_id}",
                        upstream_version=_clean(item_map.get("sha"))
                        or _clean(item_map.get("lastModified")),
                        canonical_url=self._canonical_url(endpoint, item_id),
                        observed_at=self._clock(),
                        published_at=_datetime(item_map.get("createdAt")),
                        updated_at=_datetime(item_map.get("lastModified")),
                        media_type="application/json",
                        payload=raw,
                        response_metadata=response.response_metadata,
                    )
                )
        yield ConnectorPage(
            records=tuple(records),
            next_cursor=_checkpoint(
                len(records), window, records[-1].upstream_id if records else None
            ),
            exhausted=True,
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            wrapped = _json_object(record.payload)
            kind = str(wrapped["kind"])
            raw_item = wrapped["item"]
            if not isinstance(raw_item, dict):
                raise ValueError("Hub item must be an object")
            item = cast(dict[str, object], raw_item)
            item_id = str(item["id"])
            work_type = dict(self._KINDS)[kind]
            tags = tuple(tag for tag in _object_list(item.get("tags", [])) if isinstance(tag, str))
            arxiv_ids = tuple(
                dict.fromkeys(
                    tag.split(":", 1)[1].casefold()
                    for tag in tags
                    if tag.casefold().startswith("arxiv:") and ":" in tag
                )
            )
            identities = [
                NormalizedIdentity(
                    id_type=ExternalIdType.HUGGINGFACE,
                    raw_value=f"{kind}:{item_id}",
                    normalized_value=f"{kind}:{item_id}".casefold(),
                )
            ]
            identities.extend(
                NormalizedIdentity(
                    id_type=ExternalIdType.ARXIV, raw_value=value, normalized_value=value
                )
                for value in arxiv_ids
            )
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=record.upstream_id,
                upstream_version=record.upstream_version,
                work_type=work_type,
                title=item_id,
                normalized_title=normalize_title(item_id),
                abstract=None,
                canonical_url=record.canonical_url,
                publication_status=PublicationStatus.PUBLISHED,
                published_at=record.published_at,
                updated_at=record.updated_at,
                identities=tuple(identities),
                authors=(),
                source_topics=tags,
                document_urls=(),
                repository_urls=(),
                license_hint=None,
                extra={
                    "kind": kind,
                    "arxiv_ids": list(arxiv_ids),
                    "pipeline_tag": _clean(item.get("pipeline_tag")),
                    "downloads": _integer(item.get("downloads")),
                },
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise _failure("Hugging Face", "invalid Hub metadata") from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        return (
            []
            if record.canonical_url.startswith("https://huggingface.co/")
            else ["invalid Hugging Face canonical URL"]
        )

    @staticmethod
    def _canonical_url(endpoint: str, item_id: str) -> str:
        prefix = {"datasets": "datasets/", "spaces": "spaces/"}.get(endpoint, "")
        return f"https://huggingface.co/{prefix}{item_id}"


class RssAtomConnector:
    contract_version: str = "1.0"
    key = "official-rss"
    trust_tier = TrustTier.A
    connector_version = "official-rss-v1"

    def __init__(
        self,
        http: SourceHttpClient,
        feeds: tuple[str, ...],
        *,
        source_key: str = "official-rss",
        trust_tier: TrustTier = TrustTier.A,
        artifact_kind: str = "official_post",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not feeds or any(not feed.startswith("https://") for feed in feeds):
            raise ValueError("official RSS feeds must be an HTTPS allowlist")
        self._http = http
        self.feeds = feeds
        self.key = source_key
        self.trust_tier = trust_tier
        self.artifact_kind = artifact_kind
        self._clock = clock

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        records: list[RawSourceRecord] = []
        for feed in self.feeds:
            if len(records) >= window.page_size:
                break
            response = await self._http.get(
                feed,
                source_key=self.key,
                minimum_request_interval_ms=500,
                expected_media_types=(
                    "application/atom+xml",
                    "application/rss+xml",
                    "application/xml",
                    "text/xml",
                ),
                headers={
                    "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9"
                },
            )
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as error:
                raise _failure("official RSS", "malformed XML") from error
            entries = root.findall(".//item") or root.findall("{http://www.w3.org/2005/Atom}entry")
            for entry in entries:
                if len(records) >= window.page_size:
                    break
                raw = ET.tostring(entry, encoding="utf-8")
                title = self._text(entry, "title")
                link = self._link(entry)
                if title is None or link is None or urlparse(link).scheme != "https":
                    continue
                identity = self._text(entry, "guid") or self._text(entry, "id") or link
                published = _datetime(
                    self._text(entry, "pubDate") or self._text(entry, "published")
                )
                records.append(
                    RawSourceRecord(
                        source_key=self.key,
                        upstream_id=identity,
                        upstream_version=None,
                        canonical_url=link,
                        observed_at=self._clock(),
                        published_at=published,
                        updated_at=_datetime(self._text(entry, "updated")) or published,
                        media_type="application/atom+xml",
                        payload=raw,
                        response_metadata={**response.response_metadata, "feed": feed},
                    )
                )
        yield ConnectorPage(
            records=tuple(records),
            next_cursor=_checkpoint(
                len(records), window, records[-1].upstream_id if records else None
            ),
            exhausted=True,
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            entry = ET.fromstring(record.payload)
            title = self._text(entry, "title")
            if title is None:
                raise ValueError("missing title")
            summary = (
                self._text(entry, "description")
                or self._text(entry, "summary")
                or self._text(entry, "content")
            )
            text = " ".join(filter(None, (title, summary)))
            arxiv_ids = tuple(
                dict.fromkeys(match.group(1).casefold() for match in _ARXIV.finditer(text))
            )
            repositories = tuple(
                dict.fromkeys(
                    f"https://github.com/{m.group(1)}/{m.group(2).removesuffix('.git')}"
                    for m in _GITHUB.finditer(text)
                )
            )
            transcript_urls = tuple(dict.fromkeys(_TRANSCRIPT.findall(text)))
            identities = [
                NormalizedIdentity(
                    id_type=ExternalIdType.URL,
                    raw_value=record.canonical_url,
                    normalized_value=record.canonical_url,
                )
            ]
            identities.extend(
                NormalizedIdentity(
                    id_type=ExternalIdType.ARXIV, raw_value=value, normalized_value=value
                )
                for value in arxiv_ids
            )
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=record.upstream_id,
                upstream_version=record.upstream_version,
                work_type=WorkType.ARTICLE,
                title=title,
                normalized_title=normalize_title(title),
                abstract=summary,
                canonical_url=record.canonical_url,
                publication_status=PublicationStatus.PUBLISHED,
                published_at=record.published_at,
                updated_at=record.updated_at,
                identities=tuple(identities),
                authors=(),
                source_topics=(),
                document_urls=transcript_urls if self.key == "youtube" else (),
                repository_urls=repositories,
                license_hint=None,
                extra={
                    "arxiv_ids": list(arxiv_ids),
                    "kind": self.artifact_kind,
                    "transcript_policy": "publisher_linked_only" if self.key == "youtube" else None,
                },
            )
        except (ET.ParseError, ValueError) as error:
            raise _failure("official RSS", str(error)) from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        allowed = {urlparse(feed).hostname for feed in self.feeds}
        return (
            []
            if urlparse(record.canonical_url).hostname in allowed
            else ["post URL is outside the official feed authorities"]
        )

    @staticmethod
    def _text(entry: ET.Element, local: str) -> str | None:
        for element in entry.iter():
            if element.tag.rsplit("}", 1)[-1] == local:
                return _clean("".join(element.itertext()))
        return None

    @staticmethod
    def _link(entry: ET.Element) -> str | None:
        direct = RssAtomConnector._text(entry, "link")
        if direct:
            return direct
        for element in entry.iter():
            if element.tag.rsplit("}", 1)[-1] == "link" and element.get("href"):
                return _clean(element.get("href"))
        return None


def stable_artifact_key(source_key: str, upstream_id: str) -> str:
    """Return a deterministic non-secret artifact key for idempotent persistence."""
    return hashlib.sha256(f"{source_key}\0{upstream_id}".encode()).hexdigest()


class XExportConnector:
    """Ingest only user-supplied X export records; never scrape or bypass platform access."""

    contract_version: str = "1.0"
    key = "x-watchlist"
    trust_tier = TrustTier.D
    connector_version = "x-export-v1"

    def __init__(
        self,
        items: tuple[dict[str, object], ...],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._items = items
        self._clock = clock

    async def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]:
        records: list[RawSourceRecord] = []
        for item in self._items[: window.page_size]:
            upstream_id = _clean(item.get("id"))
            url = _clean(item.get("url"))
            text = _clean(item.get("text"))
            if upstream_id is None or url is None or text is None:
                continue
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {"x.com", "twitter.com"}:
                continue
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()
            created = _datetime(item.get("created_at"))
            records.append(
                RawSourceRecord(
                    source_key=self.key,
                    upstream_id=upstream_id,
                    upstream_version=None,
                    canonical_url=url,
                    observed_at=self._clock(),
                    published_at=created,
                    updated_at=created,
                    media_type="application/json",
                    payload=payload,
                    response_metadata={"origin": "user_supplied_export"},
                )
            )
        yield ConnectorPage(
            records=tuple(records),
            next_cursor=_checkpoint(
                len(records), window, records[-1].upstream_id if records else None
            ),
            exhausted=True,
        )

    def normalize(self, record: RawSourceRecord) -> NormalizedRecord:
        try:
            item = _json_object(record.payload)
            text = str(item["text"])
            arxiv_ids = tuple(
                dict.fromkeys(match.group(1).casefold() for match in _ARXIV.finditer(text))
            )
            repositories = tuple(
                dict.fromkeys(
                    f"https://github.com/{match.group(1)}/{match.group(2).removesuffix('.git')}"
                    for match in _GITHUB.finditer(text)
                )
            )
            identities = [
                NormalizedIdentity(
                    id_type=ExternalIdType.URL,
                    raw_value=record.canonical_url,
                    normalized_value=record.canonical_url,
                )
            ]
            identities.extend(
                NormalizedIdentity(
                    id_type=ExternalIdType.ARXIV, raw_value=value, normalized_value=value
                )
                for value in arxiv_ids
            )
            return NormalizedRecord(
                source_key=self.key,
                upstream_id=record.upstream_id,
                work_type=WorkType.ARTICLE,
                title=text[:160],
                normalized_title=normalize_title(text[:160]),
                abstract=text,
                canonical_url=record.canonical_url,
                publication_status=PublicationStatus.PUBLISHED,
                published_at=record.published_at,
                updated_at=record.updated_at,
                identities=tuple(identities),
                authors=(),
                source_topics=(),
                document_urls=(),
                repository_urls=repositories,
                extra={"origin": "user_supplied_export", "author": _clean(item.get("author"))},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise _failure("X export", "invalid user-supplied record") from error

    def validate(self, record: NormalizedRecord) -> list[str]:
        return (
            []
            if urlparse(record.canonical_url).hostname in {"x.com", "twitter.com"}
            else ["invalid X export URL"]
        )
