"""Typed source connector and ingestion contracts from the phase-one specification."""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.domain.models import (
    ExternalIdType,
    JsonObject,
    PublicationStatus,
    TrustTier,
    UtcDateTime,
    WorkType,
)


class ConnectorModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FetchWindow(ConnectorModel):
    since: UtcDateTime
    until: UtcDateTime
    cursor: JsonObject | None = None
    page_size: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def validate_window(self) -> FetchWindow:
        if self.since > self.until:
            raise ValueError("fetch window since cannot be after until")
        return self


class CursorCheckpoint(ConnectorModel):
    schema_version: int = Field(default=1, ge=1)
    position: str = Field(min_length=1)
    window_end: UtcDateTime
    last_upstream_id: str | None = None


class RawSourceRecord(ConnectorModel):
    source_key: str = Field(min_length=1, max_length=255)
    upstream_id: str = Field(min_length=1)
    upstream_version: str | None = None
    canonical_url: str = Field(min_length=1)
    observed_at: UtcDateTime
    published_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    media_type: str = Field(min_length=1, max_length=255)
    payload: bytes = Field(min_length=1)
    response_metadata: JsonObject = Field(default_factory=dict)


class NormalizedIdentity(ConnectorModel):
    id_type: ExternalIdType
    raw_value: str = Field(min_length=1)
    normalized_value: str = Field(min_length=1)


class NormalizedAuthor(ConnectorModel):
    display_name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    order: int = Field(ge=1)
    orcid: str | None = None
    affiliation: str | None = None


class NormalizedRecord(ConnectorModel):
    source_key: str = Field(min_length=1)
    upstream_id: str = Field(min_length=1)
    upstream_version: str | None = None
    work_type: WorkType
    title: str = Field(min_length=1)
    normalized_title: str = Field(min_length=1)
    abstract: str | None = None
    canonical_url: str = Field(min_length=1)
    publication_status: PublicationStatus
    published_at: UtcDateTime | None = None
    updated_at: UtcDateTime | None = None
    identities: tuple[NormalizedIdentity, ...]
    authors: tuple[NormalizedAuthor, ...]
    source_topics: tuple[str, ...]
    document_urls: tuple[str, ...]
    repository_urls: tuple[str, ...]
    license_hint: str | None = None
    extra: JsonObject = Field(default_factory=dict)


class ConnectorPage(ConnectorModel):
    records: tuple[RawSourceRecord, ...]
    next_cursor: JsonObject | None
    exhausted: bool


class ConnectorErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_5XX = "UPSTREAM_5XX"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    TERMS_BLOCKED = "TERMS_BLOCKED"
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"


class ConnectorFailure(ConnectorModel):
    code: ConnectorErrorCode
    retryable: bool
    safe_message: str = Field(min_length=1, max_length=500)
    attempts: int = Field(ge=1)
    status_code: int | None = Field(default=None, ge=100, le=599)


class ConnectorException(RuntimeError):
    """Exception carrying a secret-safe structured connector failure."""

    def __init__(self, failure: ConnectorFailure) -> None:
        self.failure = failure
        super().__init__(f"{failure.code.value}: {failure.safe_message}")


class HttpResponse(ConnectorModel):
    status_code: int = Field(ge=200, le=299)
    media_type: str
    content: bytes
    response_metadata: dict[str, JsonValue]


class SourceConnector(Protocol):
    contract_version: str
    key: str
    trust_tier: TrustTier
    connector_version: str

    def fetch(self, window: FetchWindow) -> AsyncIterator[ConnectorPage]: ...
    def normalize(self, record: RawSourceRecord) -> NormalizedRecord: ...
    def validate(self, record: NormalizedRecord) -> list[str]: ...
