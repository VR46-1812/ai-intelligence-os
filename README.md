# AI Intelligence OS — Build Specification

This package is the source of truth for building a zero-subscription-cost, local-first AI research intelligence system on Rujay's Windows laptop.

## Reading order

1. `CONTEXT.md` — mission, scope, decisions, constraints, and non-goals.
2. `AGENTS.md` — concise rules Codex must obey on every task.
3. `docs/PRD.md` — product requirements, workflows, acceptance criteria, and non-functional requirements.
4. `docs/DATABASE_SCHEMA.md` and `contracts/schema.sql` — persistent data design.
5. `docs/CONNECTOR_CONTRACTS.md` — source adapter interfaces and ingestion rules.
6. `docs/RANKING_AND_REPORTS.md` — ranking formula and output contracts.
7. `docs/PHASE_ONE_BACKLOG.md` — build sequence and definition of done.
8. `prompts/CODEX_EXECUTION_PROMPTS.md` — bounded Codex prompts, one milestone at a time.
9. `docs/SETUP_AND_MODELS.md` — local setup, model acquisition, resource limits, and run procedure.

## Core engineering decision

Phase one is a modular monolith, not a distributed platform. It uses Python, FastAPI, SQLite/FTS5, local files, a React/TypeScript UI, Ollama, scheduled batch jobs, and sequential agent roles. Interfaces are designed so storage and orchestration can be replaced later without rewriting domain logic.

## Final mission

Deliver a trustworthy daily AI intelligence workspace that discovers new developments, filters noise, produces evidence-backed technical deep dives, relates research to code and business opportunities, and teaches the user enough to build production systems from the knowledge.

## V1 operator commands

From `D:\Rujay\ai-intelligence-os`:

```powershell
.\scripts\start.ps1
.\scripts\run-daily.ps1
.\scripts\release-verify.ps1
```

Open `http://127.0.0.1:5173`. Stop workspace-managed services with
`.\scripts\stop.ps1`. See `docs/V1_RELEASE.md` for backup, restore, failure
recovery, acceptance evidence, and the release checklist.
