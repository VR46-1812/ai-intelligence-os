"""Public contracts for staged reports, topics, opportunities, and evaluation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class IntelligenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelRankingSignal(IntelligenceModel):
    work_id: str
    novelty: float = Field(ge=0, le=1)
    method_depth: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    opportunity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    refinement: float = Field(ge=-2.5, le=2.5)
    fallback: bool
    evidence_ids: tuple[str, ...]
    rationale: str


class RankedReportItem(IntelligenceModel):
    work_id: str
    title: str
    score: float = Field(ge=0, le=100)
    reason: str
    status: str
    model_signal: ModelRankingSignal | None = None


class LearningPlanItem(IntelligenceModel):
    topic: str
    prerequisites: tuple[str, ...]
    estimated_minutes: int = Field(ge=10, le=180)
    recommended_item: str
    exercise: str
    evidence_ids: tuple[str, ...]


class CommercialHypothesis(IntelligenceModel):
    label: str = Field(pattern=r"^commercial_hypothesis$")
    problem: str
    target_buyer: str
    proposed_offer: str
    supporting_evidence: tuple[str, ...]
    prototype: str
    effort: str
    validation_experiment: str
    pricing_hypothesis: str
    competition: str
    risks: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class ProjectRelevance(IntelligenceModel):
    project: str
    relevance: str
    evidence_ids: tuple[str, ...]


class SourceCoverage(IntelligenceModel):
    source_key: str
    records: int = Field(ge=0)
    status: str


class PipelineReportSummary(IntelligenceModel):
    discovered: int = Field(ge=0)
    normalized: int = Field(ge=0)
    filtered: int = Field(ge=0)
    shortlisted: int = Field(ge=0)
    briefed: int = Field(ge=0)
    analyzed: int = Field(ge=0)
    failed: int = Field(ge=0)
    run_id: str


class DailyIntelligenceReport(IntelligenceModel):
    schema_version: str = Field(pattern=r"^1\.0$")
    report_date: str
    pipeline: PipelineReportSummary
    top_technical: tuple[RankedReportItem, ...]
    top_commercial: tuple[RankedReportItem, ...]
    deep_dives: tuple[str, ...]
    important_updates: tuple[dict[str, str], ...]
    learning_focus: tuple[str, ...]
    coverage_gaps: tuple[str, ...]
    executive_briefing: str = "No verified briefing is available yet."
    what_happened: tuple[str, ...] = ()
    why_it_matters: tuple[str, ...] = ()
    evidence_versus_interpretation: tuple[str, ...] = ()
    research_and_product_launches: tuple[str, ...] = ()
    community_signals: tuple[str, ...] = ()
    learning_plan: tuple[LearningPlanItem, ...] = ()
    what_to_build: tuple[str, ...] = ()
    commercial_hypotheses: tuple[CommercialHypothesis, ...] = ()
    india_market_hypotheses: tuple[str, ...] = ()
    personal_relevance: tuple[ProjectRelevance, ...] = ()
    risks_and_unknowns: tuple[str, ...] = ()
    watchlist_changes: tuple[str, ...] = ()
    source_coverage: tuple[SourceCoverage, ...] = ()
    agent_health: dict[str, str] = Field(default_factory=dict)


class StageState(IntelligenceModel):
    key: str
    order: int
    status: str
    error_code: str | None = None


class DeepDiveProgress(IntelligenceModel):
    job_id: str
    work_id: str
    status: str
    analysis_run_id: str | None
    stages: tuple[StageState, ...]


class TopicPaper(IntelligenceModel):
    work_id: str
    title: str
    score: float = Field(ge=0, le=100)


class TopicOverview(IntelligenceModel):
    key: str
    label: str
    paper_count: int = Field(ge=0)
    daily_change: int = Field(ge=0)
    papers: tuple[TopicPaper, ...]


class Opportunity(IntelligenceModel):
    kind: str
    work_id: str
    title: str
    headline: str
    detail: str
    evidence_ids: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)


class EvaluationScores(IntelligenceModel):
    version: str
    examples: int
    completeness: float
    citation_coverage: float
    repetition_rate: float
    unsupported_rejection: float
    precision_at_10: float
    ndcg_at_10: float
    passed: bool


class HumanReviewCase(IntelligenceModel):
    id: str
    category: str
    input_summary: str
    reviewer_question: str
    expected_decision: str
    pass_criteria: str


class HumanReviewSet(IntelligenceModel):
    version: str
    cases: tuple[HumanReviewCase, ...] = Field(min_length=20)
