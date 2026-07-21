"""Strict report and public analysis contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domain.models import AnalysisStatus, AnalysisType, PublicationStatus, UtcDateTime


class AnalysisModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimType(StrEnum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    RECOMMENDATION = "recommendation"
    HYPOTHESIS = "hypothesis"


class FastClaim(AnalysisModel):
    text: str = Field(min_length=1, max_length=1000)
    type: ClaimType
    evidence_ids: tuple[str, ...] = Field(max_length=12)


class FastBrief(AnalysisModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    work_id: str = Field(min_length=1)
    change: str = Field(min_length=1, max_length=320)
    problem: str = Field(min_length=8, max_length=800)
    contribution: str = Field(min_length=8, max_length=800)
    evidence_state: str = Field(pattern=r"^(strong|moderate|weak|unknown)$")
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    code_state: str = Field(pattern=r"^(official|author_linked|community|none_found|unknown)$")
    technical_relevance: str = Field(min_length=8, max_length=800)
    commercial_relevance: str = Field(min_length=8, max_length=800)
    recommended_action: str = Field(pattern=r"^(deep_dive|track|read_source|ignore|manual_review)$")
    claims: tuple[FastClaim, ...] = Field(min_length=1, max_length=20)


class ReportSection(AnalysisModel):
    markdown: str = Field(min_length=8)
    confidence: float = Field(ge=0, le=1)
    claim_ids: tuple[str, ...]


class DeepClaim(AnalysisModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    type: ClaimType
    importance: str = Field(pattern=r"^(minor|major|critical)$")
    verification_status: str = Field(pattern=r"^(unsupported|supported|conflicted|rejected)$")
    evidence_ids: tuple[str, ...]
    qualifier: str | None = None


class Reproducibility(AnalysisModel):
    status: str = Field(pattern=r"^(unknown|insufficient|partial|promising|reproduced)$")
    repository_urls: tuple[HttpUrl, ...]
    assets: tuple[str, ...]
    hardware_fit: str = Field(pattern=r"^(fits|fits_with_reduction|does_not_fit|unknown)$")
    steps: tuple[str, ...]
    risks: tuple[str, ...]


class ProductionApplication(AnalysisModel):
    name: str
    system_change: str
    expected_value: str
    risks: tuple[str, ...]
    claim_ids: tuple[str, ...]


class CommercialHypothesis(AnalysisModel):
    problem: str
    buyer: str
    workflow: str
    pilot: str
    value_metric: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...]
    unknowns: tuple[str, ...]


class LearningStep(AnalysisModel):
    concept: str
    reason: str
    sequence: int = Field(ge=1)
    source_ids: tuple[str, ...] = ()


class SkepticFinding(AnalysisModel):
    severity: str = Field(pattern=r"^(info|warning|critical)$")
    finding: str
    affected_claim_ids: tuple[str, ...]
    resolution: str = Field(pattern=r"^(accepted|qualified|rejected|unresolved)$")


class DeepDiveReport(AnalysisModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    work_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publication_status: PublicationStatus
    executive_significance: ReportSection
    problem_context: ReportSection
    method: ReportSection
    evaluation: ReportSection
    limitations: ReportSection
    reproducibility: Reproducibility
    production_applications: tuple[ProductionApplication, ...]
    commercial_hypotheses: tuple[CommercialHypothesis, ...]
    learning_path: tuple[LearningStep, ...]
    skeptic_findings: tuple[SkepticFinding, ...]
    claims: tuple[DeepClaim, ...] = Field(min_length=1)


class ModelStatus(AnalysisModel):
    runtime: str = "ollama"
    available: bool
    model: str
    model_installed: bool
    runtime_version: str | None = None
    active: bool = False
    size_vram_mb: int = Field(default=0, ge=0)
    detail: str


class AnalysisResult(AnalysisModel):
    id: str
    work_id: str
    analysis_type: AnalysisType
    status: AnalysisStatus
    cached: bool = False
    citation_coverage: float = Field(default=0, ge=0, le=1)
    citations_verified: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    safe_detail: str | None = None
    output: FastBrief | DeepDiveReport | None = None
    created_at: UtcDateTime


class RankedBrief(AnalysisModel):
    work_id: str
    title: str
    technical_score: float | None = Field(default=None, ge=0, le=100)
    brief: AnalysisResult | None = None


class TodayReport(AnalysisModel):
    report_date: str
    model: ModelStatus
    ranked: tuple[RankedBrief, ...]
    generated_count: int = Field(ge=0)
    remaining_fast_briefs: int = Field(ge=0)
    remaining_deep_dives: int = Field(ge=0)
