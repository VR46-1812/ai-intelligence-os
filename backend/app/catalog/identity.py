"""Deterministic catalog identity resolution and revision creation."""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    Author,
    ExternalIdentifier,
    ExternalIdType,
    LifecycleState,
    PageRequest,
    PublicationStatus,
    UtcDateTime,
    Work,
    WorkAuthor,
    WorkType,
    WorkVersion,
    WorkVersionFilter,
)
from app.domain.repositories import (
    CatalogIdentityRepository,
    WorkRepository,
    WorkVersionRepository,
)

_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_PATTERN = re.compile(
    r"^(?P<stable>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z-]+)?/\d{7}))(?P<version>v\d+)?$",
    re.IGNORECASE,
)
_OPENREVIEW_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_DOI_PREFIX = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
_ARXIV_PREFIX = re.compile(
    r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)", re.IGNORECASE
)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class CatalogIdentityError(ValueError):
    """Raised when catalog identity input cannot be normalized safely."""


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IdentityInput(CatalogModel):
    id_type: ExternalIdType
    raw_value: str = Field(min_length=1)


class NormalizedIdentity(CatalogModel):
    id_type: ExternalIdType
    raw_value: str
    normalized_value: str
    version_label: str | None = None


class CatalogRecord(CatalogModel):
    source_record_id: str | None = None
    work_type: WorkType
    title: str = Field(min_length=1)
    abstract: str | None = None
    language: str = Field(default="en", min_length=1)
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    published_at: UtcDateTime | None = None
    observed_at: UtcDateTime
    upstream_version: str | None = None
    content_sha256: str = Field(min_length=1)
    first_author: str | None = None
    identities: tuple[IdentityInput, ...] = Field(min_length=1)


class IdentityResolutionStatus(StrEnum):
    CREATED = "created"
    REVISION_CREATED = "revision_created"
    ALREADY_KNOWN = "already_known"
    MANUAL_REVIEW = "manual_review"


class IdentityResolution(CatalogModel):
    status: IdentityResolutionStatus
    work_id: str | None = None
    version_id: str | None = None
    candidate_work_ids: tuple[str, ...] = ()
    matched_on: tuple[str, ...] = ()


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def normalize_title(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise CatalogIdentityError("title is empty after normalization")
    return normalized


def normalize_author(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise CatalogIdentityError("author is empty after normalization")
    return normalized


def normalize_identifier(identity: IdentityInput) -> NormalizedIdentity:
    raw_value = identity.raw_value.strip()
    if identity.id_type is ExternalIdType.DOI:
        value = _DOI_PREFIX.sub("", raw_value).strip().casefold()
        if not _DOI_PATTERN.fullmatch(value) or any(character.isspace() for character in value):
            raise CatalogIdentityError(f"invalid DOI: {identity.raw_value}")
        return NormalizedIdentity(
            id_type=identity.id_type, raw_value=identity.raw_value, normalized_value=value
        )

    if identity.id_type is ExternalIdType.ARXIV:
        value = _ARXIV_PREFIX.sub("", raw_value).strip()
        if value.casefold().endswith(".pdf"):
            value = value[:-4]
        match = _ARXIV_PATTERN.fullmatch(value)
        if match is None:
            raise CatalogIdentityError(f"invalid arXiv identifier: {identity.raw_value}")
        version = match.group("version")
        return NormalizedIdentity(
            id_type=identity.id_type,
            raw_value=identity.raw_value,
            normalized_value=match.group("stable").casefold(),
            version_label=None if version is None else version.casefold(),
        )

    if identity.id_type is ExternalIdType.OPENREVIEW:
        parsed = urlparse(raw_value)
        value = raw_value
        if parsed.scheme or parsed.netloc:
            if parsed.hostname is None or parsed.hostname.casefold() not in {
                "openreview.net",
                "www.openreview.net",
            }:
                raise CatalogIdentityError("OpenReview URL must use openreview.net")
            query_id = parse_qs(parsed.query).get("id")
            if query_id is None or len(query_id) != 1:
                raise CatalogIdentityError("OpenReview URL must contain one id parameter")
            value = query_id[0]
        elif raw_value.casefold().startswith("openreview:"):
            value = raw_value.split(":", 1)[1].strip()
        if not _OPENREVIEW_PATTERN.fullmatch(value):
            raise CatalogIdentityError(f"invalid OpenReview identifier: {identity.raw_value}")
        return NormalizedIdentity(
            id_type=identity.id_type, raw_value=identity.raw_value, normalized_value=value
        )

    raise CatalogIdentityError(f"unsupported identity type for M1.3: {identity.id_type.value}")


def _encode_crockford(value: int, length: int) -> str:
    characters = ["0"] * length
    for index in range(length - 1, -1, -1):
        characters[index] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(characters)


def new_ulid() -> str:
    """Return a local, sortable ULID-style identifier without a dependency."""
    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    return _encode_crockford(timestamp_ms, 10) + _encode_crockford(secrets.randbits(80), 16)


class CatalogIdentityService:
    """Resolve exact identities and create catalog works/revisions atomically."""

    def __init__(
        self,
        works: WorkRepository,
        versions: WorkVersionRepository,
        identities: CatalogIdentityRepository,
        *,
        id_factory: Callable[[], str] = new_ulid,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        fuzzy_title_threshold: float = 0.92,
    ) -> None:
        if not 0.8 <= fuzzy_title_threshold <= 1:
            raise CatalogIdentityError("fuzzy_title_threshold must be between 0.8 and 1")
        self._works = works
        self._versions = versions
        self._identities = identities
        self._id_factory = id_factory
        self._clock = clock
        self._fuzzy_title_threshold = fuzzy_title_threshold

    def resolve(self, record: CatalogRecord) -> IdentityResolution:
        """Resolve one record; callers must provide the surrounding transaction."""
        normalized = self._normalized_identities(record.identities)
        exact_matches = tuple(
            (identity, existing)
            for identity in normalized
            if (
                existing := self._identities.get_external_id(
                    identity.id_type, identity.normalized_value
                )
            )
            is not None
        )
        exact_work_ids = {existing.work_id for _, existing in exact_matches}
        matched_on = tuple(
            f"{identity.id_type.value}:{identity.normalized_value}" for identity, _ in exact_matches
        )

        if len(exact_work_ids) > 1:
            return IdentityResolution(
                status=IdentityResolutionStatus.MANUAL_REVIEW,
                candidate_work_ids=tuple(sorted(exact_work_ids)),
                matched_on=matched_on,
            )
        if exact_work_ids:
            work_id = next(iter(exact_work_ids))
            return self._update_exact_match(record, normalized, work_id, matched_on)

        candidates = self._fingerprint_candidates(record)
        if candidates:
            return IdentityResolution(
                status=IdentityResolutionStatus.MANUAL_REVIEW,
                candidate_work_ids=candidates,
                matched_on=("title:first_author:year",),
            )
        return self._create_work(record, normalized)

    @staticmethod
    def _normalized_identities(
        identities: tuple[IdentityInput, ...],
    ) -> tuple[NormalizedIdentity, ...]:
        by_key: dict[tuple[ExternalIdType, str], NormalizedIdentity] = {}
        for identity in identities:
            normalized = normalize_identifier(identity)
            by_key[(normalized.id_type, normalized.normalized_value)] = normalized
        precedence = {
            ExternalIdType.DOI: 0,
            ExternalIdType.ARXIV: 1,
            ExternalIdType.OPENREVIEW: 2,
        }
        return tuple(sorted(by_key.values(), key=lambda item: precedence[item.id_type]))

    def _fingerprint_candidates(self, record: CatalogRecord) -> tuple[str, ...]:
        if record.first_author is None or record.published_at is None:
            return ()
        return self._identities.find_candidate_work_ids(
            normalized_title=normalize_title(record.title),
            normalized_first_author=normalize_author(record.first_author),
            publication_year=record.published_at.year,
            fuzzy_title_threshold=self._fuzzy_title_threshold,
        )

    def _version_label(
        self, record: CatalogRecord, identities: tuple[NormalizedIdentity, ...]
    ) -> str:
        if record.upstream_version is not None and record.upstream_version.strip():
            value = record.upstream_version.strip().casefold()
            return value if value.startswith("v") else f"v{value}"
        arxiv_version = next(
            (
                identity.version_label
                for identity in identities
                if identity.version_label is not None
            ),
            None,
        )
        return arxiv_version or f"sha256:{record.content_sha256.casefold()}"

    def _create_work(
        self, record: CatalogRecord, identities: tuple[NormalizedIdentity, ...]
    ) -> IdentityResolution:
        now = self._clock()
        work_id = self._id_factory()
        version_id = self._id_factory()
        work = Work(
            id=work_id,
            work_type=record.work_type,
            canonical_title=record.title,
            normalized_title=normalize_title(record.title),
            abstract=record.abstract,
            language=record.language,
            publication_status=record.publication_status,
            first_published_at=record.published_at,
            lifecycle_state=LifecycleState.NORMALIZED,
            created_at=now,
            updated_at=now,
        )
        self._works.create(work)
        version = self._versions.create_or_get(
            WorkVersion(
                id=version_id,
                work_id=work_id,
                version_label=self._version_label(record, identities),
                content_sha256=record.content_sha256,
                title=record.title,
                abstract=record.abstract,
                source_record_id=record.source_record_id,
                published_at=record.published_at,
                observed_at=record.observed_at,
                is_current=True,
            )
        ).entity
        self._works.update(work.model_copy(update={"current_version_id": version.id}))
        self._attach_identities(work_id, record.source_record_id, identities, now)
        self._attach_first_author(work_id, record, now)
        return IdentityResolution(
            status=IdentityResolutionStatus.CREATED,
            work_id=work_id,
            version_id=version.id,
        )

    def _update_exact_match(
        self,
        record: CatalogRecord,
        identities: tuple[NormalizedIdentity, ...],
        work_id: str,
        matched_on: tuple[str, ...],
    ) -> IdentityResolution:
        work = self._works.get(work_id)
        if work is None:
            raise CatalogIdentityError(f"external identity references missing work: {work_id}")
        label = self._version_label(record, identities)
        versions = self._versions.list(
            page=self._all_rows(),
            filters=self._version_filter(work_id),
        )
        existing = next((version for version in versions if version.version_label == label), None)
        now = self._clock()
        self._attach_identities(work_id, record.source_record_id, identities, now)
        if existing is not None:
            return IdentityResolution(
                status=IdentityResolutionStatus.ALREADY_KNOWN,
                work_id=work_id,
                version_id=existing.id,
                matched_on=matched_on,
            )

        for version in versions:
            if version.is_current:
                self._versions.update(version.model_copy(update={"is_current": False}))
        version = self._versions.create_or_get(
            WorkVersion(
                id=self._id_factory(),
                work_id=work_id,
                version_label=label,
                content_sha256=record.content_sha256,
                title=record.title,
                abstract=record.abstract,
                source_record_id=record.source_record_id,
                published_at=record.published_at,
                observed_at=record.observed_at,
                is_current=True,
            )
        ).entity
        self._works.update(
            work.model_copy(
                update={
                    "canonical_title": record.title,
                    "normalized_title": normalize_title(record.title),
                    "abstract": record.abstract,
                    "publication_status": record.publication_status,
                    "current_version_id": version.id,
                    "updated_at": now,
                }
            )
        )
        return IdentityResolution(
            status=IdentityResolutionStatus.REVISION_CREATED,
            work_id=work_id,
            version_id=version.id,
            matched_on=matched_on,
        )

    def _attach_identities(
        self,
        work_id: str,
        source_record_id: str | None,
        identities: tuple[NormalizedIdentity, ...],
        now: datetime,
    ) -> None:
        for identity in identities:
            result = self._identities.create_external_id_or_get(
                ExternalIdentifier(
                    id=self._id_factory(),
                    work_id=work_id,
                    id_type=identity.id_type,
                    normalized_value=identity.normalized_value,
                    raw_value=identity.raw_value,
                    source_record_id=source_record_id,
                    created_at=now,
                )
            )
            if result.entity.work_id != work_id:
                raise CatalogIdentityError("external identity changed ownership during resolution")

    def _attach_first_author(self, work_id: str, record: CatalogRecord, now: datetime) -> None:
        if record.first_author is None:
            return
        author = self._identities.create_author(
            Author(
                id=self._id_factory(),
                normalized_name=normalize_author(record.first_author),
                display_name=record.first_author,
                created_at=now,
                updated_at=now,
            )
        )
        self._identities.create_work_author(
            WorkAuthor(
                work_id=work_id,
                author_id=author.id,
                author_order=1,
            )
        )

    @staticmethod
    def _all_rows() -> PageRequest:
        return PageRequest(limit=100)

    @staticmethod
    def _version_filter(work_id: str) -> WorkVersionFilter:
        return WorkVersionFilter(work_id=work_id)
