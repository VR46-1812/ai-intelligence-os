"""Typed, public-safe catalog read contracts for stored research papers."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import ExternalIdType, PublicationStatus, UtcDateTime
from app.multisource.models import LinkedSourceEvidence

_SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CatalogSort(StrEnum):
    NEWEST = "newest"
    OLDEST = "oldest"
    TITLE = "title"
    UPDATED = "updated"
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    DEEP_DIVE = "deep_dive"


class CatalogPaperQuery(CatalogModel):
    q: str | None = Field(default=None, max_length=200)
    topic: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = Field(default=None, min_length=1, max_length=255)
    source_type: str | None = Field(default=None, min_length=1, max_length=50)
    minimum_authority: float | None = Field(default=None, ge=0, le=1)
    minimum_corroboration: float | None = Field(default=None, ge=0, le=1)
    linked_only: bool = False
    published_from: date | None = None
    published_to: date | None = None
    sort: CatalogSort = CatalogSort.NEWEST
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100_000)

    @field_validator("q", "topic", "source", "source_type")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_dates_and_search(self) -> Self:
        if self.published_from and self.published_to and self.published_from > self.published_to:
            raise ValueError("published_from cannot be after published_to")
        if self.q is not None and not _SEARCH_TOKEN.search(self.q):
            raise ValueError("q must contain at least one searchable letter or number")
        return self


class CatalogAuthor(CatalogModel):
    display_name: str
    order: int = Field(ge=1)
    orcid: str | None = None


class CatalogIdentity(CatalogModel):
    id_type: ExternalIdType
    value: str
    external_url: str | None = None


class CatalogTopic(CatalogModel):
    key: str
    name: str


class CatalogRanking(CatalogModel):
    technical: float | None = Field(default=None, ge=0, le=100)
    commercial: float | None = Field(default=None, ge=0, le=100)
    deep_dive_priority: float | None = Field(default=None, ge=0, le=100)
    technical_components: dict[str, float] = Field(default_factory=dict)
    calculated_at: UtcDateTime | None = None


class CatalogPaper(CatalogModel):
    id: str
    title: str
    abstract: str | None
    publication_status: PublicationStatus
    published_at: UtcDateTime | None
    submitted_at: UtcDateTime | None
    arxiv_announced_at: UtcDateTime | None
    locally_ingested_at: UtcDateTime
    updated_at: UtcDateTime
    current_version: str
    authors: tuple[CatalogAuthor, ...]
    identities: tuple[CatalogIdentity, ...]
    topics: tuple[CatalogTopic, ...]
    source_key: str
    source_name: str
    external_url: str | None
    match_reason: str | None = None
    document_status: str
    evidence_count: int = Field(ge=0)
    ranking: CatalogRanking
    linked_sources: tuple[LinkedSourceEvidence, ...] = ()


class CatalogPaperPage(CatalogModel):
    items: tuple[CatalogPaper, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    has_more: bool


class CatalogSourceOption(CatalogModel):
    key: str
    name: str


class CatalogFilterOptions(CatalogModel):
    topics: tuple[CatalogTopic, ...]
    sources: tuple[CatalogSourceOption, ...]
