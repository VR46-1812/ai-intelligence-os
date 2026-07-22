"""Versioned registry for the fourteen sequential logical agents."""

from app.agents.models import AgentBudget, AgentSpec


def _spec(
    order: int,
    agent_id: str,
    name: str,
    responsibility: str,
    *,
    model: bool = False,
    output_tokens: int = 0,
    prompt_version: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        version="1.0",
        order=order,
        name=name,
        responsibility=responsibility,
        model_assisted=model,
        prompt_version=prompt_version if model else None,
        budget=AgentBudget(
            timeout_seconds=600 if model else 180,
            maximum_input_tokens=4096 if model else 0,
            maximum_output_tokens=output_tokens,
            maximum_ram_mb=2048,
            maximum_vram_mb=6500 if model else 0,
        ),
    )


AGENT_SPECS: tuple[AgentSpec, ...] = (
    _spec(
        1,
        "orchestrator",
        "Orchestrator Agent",
        "Validate the bounded run and coordinate checkpoints.",
    ),
    _spec(
        2,
        "source_scout",
        "Source Scout Agent",
        "Discover bounded records through registered source connectors.",
    ),
    _spec(
        3,
        "curator",
        "Curator Agent",
        "Validate normalized records and preserve deterministic source trust labels.",
    ),
    _spec(
        4,
        "event_linker",
        "Cross-Source Event Linker Agent",
        "Link only exact identities and explicit source relationships.",
    ),
    _spec(
        5,
        "trend_ranking",
        "Trend and Ranking Agent",
        "Compute deterministic ranking and trend signals.",
    ),
    _spec(6, "evidence", "Evidence Agent", "Acquire and index bounded primary-document evidence."),
    _spec(
        7,
        "technical_analyst",
        "Technical Analyst Agent",
        "Produce evidence-grounded Scout analysis.",
        model=True,
        output_tokens=1200,
        prompt_version="fast_brief.v1+deep_dive.v1",
    ),
    _spec(
        8,
        "skeptic_verifier",
        "Skeptic and Claim-Verification Agent",
        "Reject unsupported claims and verify citations.",
    ),
    _spec(9, "learning", "Learning Agent", "Create a prerequisite-aware bounded learning plan."),
    _spec(
        10,
        "commercial_opportunity",
        "Commercial Opportunity Agent",
        "Create explicitly labelled commercial hypotheses.",
    ),
    _spec(
        11,
        "india_market",
        "India Market Agent",
        "Assess Indian buyers, validation and pricing hypotheses.",
    ),
    _spec(
        12,
        "personal_relevance",
        "Personal Relevance Agent",
        "Map verified developments to the four configured projects.",
    ),
    _spec(
        13,
        "daily_editor",
        "Daily Editor Agent",
        "Assemble one non-duplicated daily intelligence report.",
    ),
    _spec(
        14,
        "operations_watchtower",
        "Operations Watchtower Agent",
        "Summarize health, degraded sources and retention outcomes.",
    ),
)

AGENT_BY_ID = {spec.agent_id: spec for spec in AGENT_SPECS}
