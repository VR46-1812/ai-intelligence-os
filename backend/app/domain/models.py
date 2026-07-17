"""Framework-independent domain models for persisted phase-one records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_utc_datetime)]
Identifier = Annotated[str, Field(min_length=1, max_length=255)]
NonEmptyText = Annotated[str, Field(min_length=1)]
JsonObject = dict[str, JsonValue]


class DomainModel(BaseModel):
    """Immutable, strictly typed base for persistence-facing domain records."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TrustTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SourceHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


class Source(DomainModel):
    id: Identifier
    source_key: Identifier
    display_name: NonEmptyText
    trust_tier: TrustTier
    base_url: str | None = None
    enabled: bool = True
    poll_interval_minutes: Annotated[int, Field(gt=0)]
    minimum_request_interval_ms: Annotated[int, Field(ge=0)] = 0
    connector_version: Identifier
    config: JsonObject = Field(default_factory=dict)
    cursor: JsonObject | None = None
    health_status: SourceHealth = SourceHealth.UNKNOWN
    last_attempt_at: UtcDateTime | None = None
    last_success_at: UtcDateTime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class NormalizationStatus(StrEnum):
    PENDING = "pending"
    NORMALIZED = "normalized"
    REJECTED = "rejected"
    FAILED = "failed"


class SourceRecord(DomainModel):
    id: Identifier
    source_id: Identifier
    upstream_id: NonEmptyText
    upstream_version: str | None = None
    canonical_url: NonEmptyText
    payload_sha256: NonEmptyText
    raw_payload_path: NonEmptyText
    observed_at: UtcDateTime
    published_at: UtcDateTime | None = None
    updated_at_upstream: UtcDateTime | None = None
    normalization_status: NormalizationStatus = NormalizationStatus.PENDING
    error_code: str | None = None
    error_detail: str | None = None


class WorkType(StrEnum):
    PAPER = "paper"
    MODEL = "model"
    DATASET = "dataset"
    REPOSITORY = "repository"
    ARTICLE = "article"
    RELEASE = "release"
    OTHER = "other"


class PublicationStatus(StrEnum):
    UNKNOWN = "unknown"
    PREPRINT = "preprint"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class LifecycleState(StrEnum):
    DISCOVERED = "discovered"
    NORMALIZED = "normalized"
    SHORTLISTED = "shortlisted"
    ACQUIRED = "acquired"
    PARSED = "parsed"
    BRIEFED = "briefed"
    ANALYZED = "analyzed"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    FILTERED = "filtered"
    FAILED = "failed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Work(DomainModel):
    id: Identifier
    work_type: WorkType
    canonical_title: NonEmptyText
    normalized_title: NonEmptyText
    abstract: str | None = None
    language: Identifier = "en"
    publication_status: PublicationStatus = PublicationStatus.UNKNOWN
    first_published_at: UtcDateTime | None = None
    current_version_id: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.DISCOVERED
    created_at: UtcDateTime
    updated_at: UtcDateTime


class WorkVersion(DomainModel):
    id: Identifier
    work_id: Identifier
    version_label: NonEmptyText
    content_sha256: str | None = None
    title: NonEmptyText
    abstract: str | None = None
    metadata: JsonObject = Field(default_factory=dict)
    source_record_id: str | None = None
    published_at: UtcDateTime | None = None
    observed_at: UtcDateTime
    is_current: bool = False


class DocumentRole(StrEnum):
    PAPER_PDF = "paper_pdf"
    PAPER_HTML = "paper_html"
    SOURCE = "source"
    SUPPLEMENT = "supplement"
    MODEL_CARD = "model_card"
    README = "readme"
    OTHER = "other"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class Document(DomainModel):
    id: Identifier
    work_version_id: Identifier
    document_role: DocumentRole
    source_url: NonEmptyText
    local_path: NonEmptyText
    media_type: NonEmptyText
    byte_size: Annotated[int, Field(ge=0)]
    sha256: NonEmptyText
    parser_name: str | None = None
    parser_version: str | None = None
    parse_status: ParseStatus = ParseStatus.PENDING
    page_count: Annotated[int, Field(ge=0)] | None = None
    acquired_at: UtcDateTime
    parsed_at: UtcDateTime | None = None


class RankingScoreKind(StrEnum):
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    DEEP_DIVE_PRIORITY = "deep_dive_priority"


class RankingProfile(DomainModel):
    id: Identifier
    profile_key: Identifier
    version: Annotated[int, Field(gt=0)]
    weights: JsonObject
    normalization: JsonObject
    active: bool = True
    created_at: UtcDateTime


class RankingResult(DomainModel):
    id: Identifier
    work_id: Identifier
    profile_id: Identifier
    score_kind: RankingScoreKind
    total_score: Annotated[float, Field(ge=0, le=100)]
    components: JsonObject
    feature_snapshot: JsonObject
    calculated_at: UtcDateTime


class AnalysisType(StrEnum):
    FAST_BRIEF = "fast_brief"
    DEEP_DIVE = "deep_dive"
    SKEPTIC_REVIEW = "skeptic_review"
    BUSINESS_ANALYSIS = "business_analysis"
    CODE_ANALYSIS = "code_analysis"


class AnalysisStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"


class AnalysisRun(DomainModel):
    id: Identifier
    work_id: Identifier
    work_version_id: Identifier
    analysis_type: AnalysisType
    status: AnalysisStatus
    model_profile_id: str | None = None
    prompt_version_id: str | None = None
    input_fingerprint: NonEmptyText
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    error_code: str | None = None
    error_detail: str | None = None
    output: JsonObject | None = None
    created_at: UtcDateTime


class PipelineRunType(StrEnum):
    DISCOVER = "discover"
    NORMALIZE = "normalize"
    RANK = "rank"
    BRIEF = "brief"
    DEEP_DIVE = "deep_dive"
    DAILY = "daily"
    CLEANUP = "cleanup"


class PipelineTriggerType(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    RETRY = "retry"


class PipelineStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"


class PipelineRun(DomainModel):
    id: Identifier
    run_type: PipelineRunType
    trigger_type: PipelineTriggerType
    status: PipelineStatus
    config_snapshot: JsonObject
    queued_at: UtcDateTime
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    error_summary: str | None = None


class PageRequest(DomainModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class SourceFilter(DomainModel):
    enabled: bool | None = None
    health_status: SourceHealth | None = None
    trust_tier: TrustTier | None = None


class SourceRecordFilter(DomainModel):
    source_id: str | None = None
    normalization_status: NormalizationStatus | None = None


class WorkFilter(DomainModel):
    work_type: WorkType | None = None
    publication_status: PublicationStatus | None = None
    lifecycle_state: LifecycleState | None = None


class WorkVersionFilter(DomainModel):
    work_id: str | None = None
    is_current: bool | None = None


class DocumentFilter(DomainModel):
    work_version_id: str | None = None
    document_role: DocumentRole | None = None
    parse_status: ParseStatus | None = None


class RankingProfileFilter(DomainModel):
    profile_key: str | None = None
    active: bool | None = None


class RankingResultFilter(DomainModel):
    work_id: str | None = None
    profile_id: str | None = None
    score_kind: RankingScoreKind | None = None


class AnalysisRunFilter(DomainModel):
    work_id: str | None = None
    analysis_type: AnalysisType | None = None
    status: AnalysisStatus | None = None


class PipelineRunFilter(DomainModel):
    run_type: PipelineRunType | None = None
    trigger_type: PipelineTriggerType | None = None
    status: PipelineStatus | None = None
