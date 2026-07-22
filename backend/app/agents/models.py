"""Typed contracts for resumable logical-agent executions."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import JsonObject, UtcDateTime


class AgentModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentBudget(AgentModel):
    timeout_seconds: int = Field(ge=1, le=1800)
    maximum_input_tokens: int = Field(ge=0, le=8192)
    maximum_output_tokens: int = Field(ge=0, le=4096)
    maximum_ram_mb: int = Field(ge=64, le=2048)
    maximum_vram_mb: int = Field(ge=0, le=6500)


class AgentRetryPolicy(AgentModel):
    maximum_attempts: int = Field(default=2, ge=1, le=2)
    resume_from_checkpoint: bool = True


class AgentSpec(AgentModel):
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    version: str = Field(pattern=r"^1\.0$")
    order: int = Field(ge=1, le=14)
    name: str
    responsibility: str
    input_schema: str = "AgentInput@1.0"
    output_schema: str = "AgentOutput@1.0"
    model_assisted: bool
    prompt_version: str | None
    budget: AgentBudget
    retry: AgentRetryPolicy = AgentRetryPolicy()


class AgentInput(AgentModel):
    pipeline_run_id: str
    report_date: str
    checkpoint: JsonObject = Field(default_factory=dict)


class AgentOutput(AgentModel):
    summary: str
    values: JsonObject = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)


class AgentExecution(AgentModel):
    id: str
    pipeline_run_id: str
    agent_id: str
    agent_version: str
    stage_order: int
    responsibility: str
    status: AgentStatus
    idempotency_key: str
    attempt: int
    input: JsonObject
    output: JsonObject | None
    evidence_refs: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    metrics: Mapping[str, float]
    safe_failure_reason: str | None
    started_at: UtcDateTime | None
    completed_at: UtcDateTime | None


class AgentRunView(AgentModel):
    pipeline_run_id: str | None
    current_agent: str | None
    latest_success_at: UtcDateTime | None
    executions: tuple[AgentExecution, ...]
    degraded_sources: tuple[str, ...]
