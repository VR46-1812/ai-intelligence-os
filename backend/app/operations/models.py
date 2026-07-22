"""Public-safe contracts for daily orchestration, scheduling, and retention."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.analysis.models import ModelStatus
from app.domain.models import JsonObject, PipelineStatus, PipelineTriggerType, UtcDateTime


class OperationsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DailyCounts(OperationsModel):
    fetched: int = Field(default=0, ge=0)
    normalized: int = Field(default=0, ge=0)
    documents_processed: int = Field(default=0, ge=0)
    documents_failed: int = Field(default=0, ge=0)
    evidence_spans: int = Field(default=0, ge=0)
    works_ranked: int = Field(default=0, ge=0)
    briefs_generated: int = Field(default=0, ge=0)
    briefs_cached: int = Field(default=0, ge=0)
    deep_dives_generated: int = Field(default=0, ge=0)
    deep_dives_cached: int = Field(default=0, ge=0)
    files_cleaned: int = Field(default=0, ge=0)


class DailyRunResult(OperationsModel):
    run_id: str
    status: PipelineStatus
    trigger: PipelineTriggerType
    counts: DailyCounts
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None
    safe_detail: str | None = None


class DailyRunStatus(OperationsModel):
    scheduler_enabled: bool
    schedule: str
    running: bool
    current_run_id: str | None = None
    latest_run: DailyRunResult | None = None
    latest_success_at: UtcDateTime | None = None
    next_run_at: UtcDateTime | None = None


class CleanupResult(OperationsModel):
    dry_run: bool
    files_selected: int = Field(ge=0)
    bytes_selected: int = Field(ge=0)
    files_deleted: int = Field(ge=0)
    bytes_deleted: int = Field(ge=0)
    storage_bytes_before: int = Field(ge=0)
    storage_bytes_after: int = Field(ge=0)
    storage_budget_bytes: int = Field(ge=0)
    budget_exceeded: bool


class SourceOperationalStatus(OperationsModel):
    source_key: str
    health: str
    checkpoint: JsonObject | None
    last_attempt_at: UtcDateTime | None
    last_success_at: UtcDateTime | None


class ResourceBudgetStatus(OperationsModel):
    non_llm_ram_mb: int
    normal_total_ram_mb: int
    temporary_peak_ram_mb: int
    reserved_windows_ram_mb: int
    vram_target_mb: int
    download_concurrency: int
    generation_concurrency: int
    maximum_storage_gib: int


class PublicFailure(OperationsModel):
    kind: str
    run_id: str
    occurred_at: UtcDateTime
    safe_detail: str
    retryable: bool


class SystemStatus(OperationsModel):
    daily: DailyRunStatus
    source: SourceOperationalStatus
    model: ModelStatus
    resources: ResourceBudgetStatus
    storage_bytes: int = Field(ge=0)
    failures: tuple[PublicFailure, ...]
