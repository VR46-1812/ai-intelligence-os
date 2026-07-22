"""Public-safe contracts for multi-source discovery and linked events."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import PipelineStatus, UtcDateTime


class MultiSourceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceSyncCount(MultiSourceModel):
    source_key: str
    status: PipelineStatus
    fetched: int = Field(ge=0)
    created: int = Field(ge=0)
    normalized: int = Field(ge=0)
    linked: int = Field(ge=0)
    safe_message: str | None = None


class MultiSourceSyncResult(MultiSourceModel):
    sources: tuple[SourceSyncCount, ...]
    total_fetched: int = Field(ge=0)
    total_normalized: int = Field(ge=0)
    events_updated: int = Field(ge=0)


class LinkedSourceEvidence(MultiSourceModel):
    artifact_id: str
    source_key: str
    artifact_type: str
    source_type: str
    title: str
    canonical_url: str
    relationship: str
    confidence: float = Field(default=1, ge=0, le=1)
    matching_evidence: tuple[str, ...] = ()
    content_class: str
    authority: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    published_at: UtcDateTime | None = None


class LinkedEvent(MultiSourceModel):
    id: str
    title: str
    primary_work_id: str | None
    occurred_at: UtcDateTime | None
    corroboration: float = Field(ge=0, le=1)
    source_count: int = Field(ge=0)
    classification: str
    corroboration_status: str
    association_confidence: float = Field(ge=0, le=1)
    linkage_reason: str
    sources: tuple[LinkedSourceEvidence, ...]


class LinkedEventPage(MultiSourceModel):
    items: tuple[LinkedEvent, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0)
    has_more: bool
