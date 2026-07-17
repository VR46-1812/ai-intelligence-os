# Phase-One Implementation Backlog

## Execution policy

- Build vertical, testable slices.
- One Codex task should normally cover one backlog item.
- Each item ends with working code, tests, and a short handoff.
- Do not ask Codex to “build the whole system” in one turn.
- Estimated effort is relative engineering size, not a promise of elapsed time.

## Milestone 0 — Repository and decision baseline

### M0.1 Repository scaffold — S

Deliver:

- `backend/`, `frontend/`, `data/`, `docs/`, `scripts/`, `tests/` layout.
- Python project managed with `uv` and Python 3.12.
- React/TypeScript/Vite frontend.
- `.env.example`, `.gitignore`, license decision placeholder, root task commands.
- Existing `AGENTS.md` and specification copied into repository.

Acceptance:

- Backend health test runs.
- Frontend builds.
- No paid dependency or cloud configuration.
- Data/model files excluded from Git.

### M0.2 Configuration and paths — S

Deliver typed settings for data root, database, download limits, concurrency, source configuration, Ollama URL, model profiles, and resource limits.

Acceptance:

- Invalid settings fail at startup with actionable errors.
- Paths cannot escape configured project/data roots.
- Secrets are redacted from settings representation.

### M0.3 Quality toolchain — S

Deliver Ruff, Pyright, Pytest, frontend ESLint, Vitest, Playwright smoke configuration, and unified local check scripts.

## Milestone 1 — Persistence and catalog

### M1.1 SQLite connection and migrations — M

Implement WAL, foreign keys, busy timeout, migration table, first migration from `contracts/schema.sql`, transaction helper, and backup command.

Acceptance:

- New database migrates cleanly.
- Re-running migrations is safe.
- Foreign key violation test fails correctly.
- Previous fixture database migration test exists for later releases.

### M1.2 Domain models and repositories — L

Implement typed domain models and repository interfaces for sources, raw records, works, versions, documents, rankings, analyses, and pipeline runs.

Acceptance:

- Domain layer does not import FastAPI or connector implementations.
- Repository tests cover create/read/update constraints and transaction rollback.

### M1.3 Catalog identity service — L

Implement identifier normalization, exact matching, revision creation, conservative fingerprint candidates, and manual-review state.

Acceptance:

- DOI/arXiv/OpenReview cases pass fixtures.
- Fuzzy match never auto-merges below explicit threshold/rules.
- Re-ingesting same record is idempotent.

### M1.4 Topic taxonomy seed — S

Create versioned controlled topics aligned with `CONTEXT.md`, source-category mappings, and user weights.

## Milestone 2 — Connector framework and arXiv

### M2.1 Connector protocol and ingestion runner — L

Implement contract types, source registry loading, raw payload persistence, cursor checkpoints, bounded retries, rate limiting, and structured errors.

Acceptance:

- Fixture connector demonstrates pagination and failure recovery.
- Cursor advances only after durable raw capture.
- Credentials do not appear in logs.

### M2.2 arXiv connector — L

Implement official metadata retrieval for configured categories and normalization contract.

Acceptance:

- Minimum three-second request interval is enforced.
- Stable ID and revision behavior pass fixtures.
- Live smoke command retrieves a bounded page.
- Repeat run does not duplicate works.

### M2.3 Discovery CLI/API — M

Add commands/endpoints to list sources, start a bounded sync, inspect a run, and view connector health.

## Milestone 3 — OpenReview and GitHub enrichment

### M3.1 OpenReview v2 connector — L

Implement allowlisted venue sync and submission/review/decision normalization.

Acceptance:

- Blind author protection test.
- Submission is not marked accepted without decision evidence.
- Venue schema variations handled by fixtures.

### M3.2 Repository link resolver — M

Extract repository links only from explicit metadata/evidence and classify relationship.

### M3.3 GitHub enrichment — L

Fetch objective metadata for shortlisted linked repositories with caching and conditional requests.

Acceptance:

- No repository code execution or automatic dependency install.
- Archived/fork/license/release/test signals stored.
- Rate-limit state is visible.

## Milestone 4 — Documents and evidence

### M4.1 Safe document downloader — M

Implement domain allowlist/policy, size/media limits, streaming hash, atomic rename, quarantine, and cleanup metadata.

### M4.2 PDF parser baseline — L

Use PyMuPDF to extract page text and section heuristics. Preserve page numbers and spans. GROBID remains optional.

Acceptance:

- Two-column paper fixture produces usable ordered text.
- Empty/image-only pages are reported, not invented.
- Parsing failure retains document metadata.

### M4.3 Evidence store and reader API — M

Persist spans and expose paginated evidence retrieval with exact source metadata.

### M4.4 FTS5 indexing — M

Index works and evidence; add query parser with safe filters and match explanations.

## Milestone 5 — Local inference foundation

### M5.1 Ollama adapter — M

Implement runtime health, model availability, structured generation, context/output limits, timeouts, cancellation, and unload behavior.

Acceptance:

- No Ollama installed/model missing produces actionable status rather than application crash.
- One-generation semaphore enforced.
- Schema-invalid response follows one repair attempt only.

### M5.2 Model profile and resource governor — M

Implement scout/analyst/embed profiles, queue, memory/VRAM snapshots where available, daily budgets, and defer behavior.

### M5.3 Prompt registry — M

Store prompts as small versioned templates with required input/output contracts. Hash and record prompt versions.

### M5.4 Classification agent — M

Create controlled-topic/relevance classifier after deterministic rules. Persist confidence and explanation.

## Milestone 6 — Ranking

### M6.1 Deterministic feature service — L

Implement freshness, source quality, code availability, topic relevance, duplication, and existing-analysis features.

### M6.2 Model-assisted feature service — M

Implement structured novelty, method-depth, impact, and opportunity hypotheses with confidence shrinkage.

### M6.3 Ranking engine — L

Implement formulas, penalties, profile versioning, replay, top-list queries, and component explanations.

### M6.4 Golden ranking set — M

Seed at least 50 examples in phase-one release candidate, with a path to 100. Implement precision@10 and nDCG@10 evaluation.

## Milestone 7 — Analysis and verification

### M7.1 Fast brief — M

Generate schema-valid fast briefs from selected evidence, not raw uncontrolled text.

### M7.2 Deep-dive stage pipeline — XL

Implement persisted stages: inventory, evidence extraction, method, evaluation, code, production, commercial, learning, skeptic, synthesis.

Acceptance:

- Resume from the last successful stage.
- Final synthesis cannot introduce an unseen claim.
- Target two complete deep dives within daily resource budget.

### M7.3 Claim/evidence linker — L

Create claim records, evidence relationships, citation coverage calculation, and verification gate.

### M7.4 Skeptic reviewer — L

Implement structured findings and critical-block behavior.

### M7.5 Daily report assembler — M

Assemble pipeline summary, ranked cards, deep dives, revisions, opportunities, learning focus, and failures.

## Milestone 8 — Professional UI

### M8.1 Design system and application shell — L

Deliver responsive navigation, typography, spacing, colors, cards, tables, status/confidence components, loading/empty/error states, and reduced motion.

### M8.2 Today screen — L

Pipeline funnel, top technical/commercial lists, deep-dive cards, latest revisions, system warnings.

### M8.3 Explore screen — L

Search, filters, sortable results, match explanation, saved topic preferences.

### M8.4 Deep Dive screen — XL

Structured reader, evidence drawer, report tabs, claims, skeptic findings, reproduction and business views.

### M8.5 System screen — M

Source health, model availability, current queue, job history, storage, resource usage, and manual controls.

### M8.6 Responsive/accessibility verification — M

Playwright smoke tests at desktop and mobile widths; keyboard and focus review.

## Milestone 9 — Daily operation and release hardening

### M9.1 Scheduler — M

Daily orchestration with manual override, no overlapping runs, and deferred analysis under resource pressure.

### M9.2 Cleanup and retention — M

Temporary PDF cleanup, maximum storage budget, stale raw-payload retention policy, and safe dry-run mode.

### M9.3 Backup/restore — M

Consistent SQLite backup plus data manifest; documented restore drill.

### M9.4 End-to-end acceptance — L

Run complete pipeline on fixtures and bounded live sources; measure accuracy, citation coverage, time, RAM, and VRAM.

### M9.5 Release documentation — M

Installation, troubleshooting, operations runbook, architecture decision records, and known limitations.

## Recommended Codex session sequence

1. M0.1 only.
2. M0.2 + M0.3 if the first scaffold is stable.
3. M1.1.
4. M1.2.
5. M1.3 + M1.4.
6. M2.1.
7. M2.2.
8. Continue one item at a time.

At every milestone boundary, run a review-only Codex task that checks architecture drift, dependency weight, tests, security, and resource assumptions before continuing.
