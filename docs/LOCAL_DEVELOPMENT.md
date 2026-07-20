# Local development

The M0 foundation provides a FastAPI health endpoint, typed local configuration,
safe data paths, and a React/Vite application shell. It does not include
persistence, connectors, model execution, or analysis pipelines.

## Prerequisites

- Python 3.12
- `uv`
- Node.js and npm

All project environments, installed packages, caches, and build output are kept
under the repository root. The PowerShell scripts configure tool caches for this
purpose.

## Install and verify

From the repository root:

```powershell
.\scripts\check.ps1
```

This single quality command synchronizes frozen dependencies, then runs Pytest,
Ruff lint/format, Pyright, Vitest, ESLint, the production frontend build,
Playwright configuration discovery, and the bounded local runtime smoke check.
Playwright browser binaries are intentionally not downloaded by this command.

Run a bounded local startup smoke test with:

```powershell
.\scripts\smoke.ps1
```

The configured Playwright smoke test can be run separately from `frontend/`
with `npm run test:e2e` after a developer explicitly installs a Chromium binary.
M0.3 validates the configuration with `npm run test:e2e:list` and does not
download or launch a Playwright browser.

## Start locally

Open two PowerShell terminals at the repository root.

Terminal one:

```powershell
.\scripts\start-backend.ps1
```

Terminal two:

```powershell
.\scripts\start-frontend.ps1
```

Open `http://127.0.0.1:5173`. The API documentation is at
`http://127.0.0.1:8000/docs`, and the health contract is available at
`http://127.0.0.1:8000/health`.

## Process five stored arXiv papers

After a bounded arXiv sync has stored metadata, acquire and extract at most five
current PDFs and calculate deterministic rankings:

```powershell
Set-Location D:\Rujay\ai-intelligence-os\backend
uv run python -m app.documents.cli --limit 5
```

The command accepts only limits from 1 through 25. Downloads are HTTPS-only from
approved arXiv hosts, capped by the configured byte limit, streamed with SHA-256,
and run within the configured maximum concurrency. It does not invoke Ollama or OCR.
Rerunning skips current versions that already have parsed evidence and creates a
new active ranking-profile version without mutating prior ranking results.

Explore reads ranking and document state through `GET /items`, paper evidence through
`GET /items/{id}/evidence`, and can replay the bounded deterministic rank stage with
`POST /pipeline/rank?limit=100`.

The Vite development server proxies `/api` requests to the local backend. To
override the frontend API base path, copy `.env.example` to `.env` and update
`VITE_API_BASE_URL`.

## Backend configuration

Backend settings are immutable Pydantic models loaded from environment variables
or the repository-root `.env` file. Variable names use the form
`AIOS_<SECTION>__<FIELD>`; `.env.example` lists the supported M0.2 surface.
Invalid values stop application creation with field-level validation details.

Writable locations are resolved beneath `D:\Rujay\ai-intelligence-os\data` by
default. The project root is fixed, and traversal or absolute paths outside the
repository/data boundaries are rejected before directory creation. Application
startup explicitly and idempotently creates only these directories:

- Database parent directory.
- Raw and processed document directories.
- Quarantine and temporary directories.

## SQLite persistence

Application startup opens the configured SQLite file and applies pending
numbered migrations from `backend/app/db/migrations`. Every connection enables
WAL journal mode, foreign-key enforcement, and a bounded busy timeout. Applied
migration names and SHA-256 checksums are recorded in `schema_migrations`; an
edited or missing applied migration stops startup rather than silently drifting.

The initial `0001_initial.sql` migration mirrors `contracts/schema.sql`,
including the FTS5 search table. M1.2 adds framework-independent Pydantic domain
models and typed SQLite repository boundaries for sources, raw source records,
works, versions, documents, rankings, analyses, and pipeline runs. Repository
writes require a caller-owned explicit transaction from `app.db.connection`;
this allows one unit of work to update several repositories and roll back as a
whole. List methods use validated, bounded offset pagination and typed filters.
No ingestion, connector, scheduling, or analysis execution is included.

M1.3 adds the deterministic catalog identity service. It normalizes DOI, arXiv,
and OpenReview identifiers, auto-links only exact external identities, creates
new versions under stable works, and returns explicit manual-review candidates
for conservative title/first-author/year matches or conflicting identifiers.
Catalog writes share the caller's explicit SQLite transaction; connector
fetching and a persistent review queue remain outside this slice.

M1.4 packages taxonomy version `2026.1`, aligned with the priorities in
`CONTEXT.md`. Application startup idempotently seeds its controlled hierarchy
into SQLite. The validated taxonomy also exposes deterministic arXiv-category
mappings and bounded per-topic user weights; unmapped categories resolve to the
explicit `unknown` topic. No classification model or connector runs in this
slice.

## Connector framework and arXiv

M2.1 provides typed connector/page contracts, persisted source-registry
loading, a bounded asynchronous HTTP transport, and a transactional ingestion
runner. HTTP calls use the configured connect/read timeouts, at most three
total attempts, exponential jittered backoff, connector request spacing, the existing
response-size ceiling, and the configured maximum of three concurrent source
downloads. Redirects are not followed automatically.

Each raw response is durably written beneath the configured raw data root with
a SHA-256 name and immutable provenance sidecar before its `source_records` row
is committed. A page cursor advances in the same SQLite transaction as all rows
from that page. Failed later pages retain the previous durable checkpoint.

Run the bounded offline demonstration from `backend/`:

```powershell
uv run python -m app.ingestion.demo --records 5
```

This stores at most five generated fixture records in the configured local data
root and database. It makes no network request and remains the default.

M2.2 adds the official Atom API connector, configured phase-one category
filtering, checkpointed pagination, and transactional normalization into works,
versions, authors, identities, and controlled topics. To explicitly permit one
live page of at most five records, run from `backend/`:

```powershell
uv run python -m app.ingestion.demo --live --records 5
```

The live command uses the shared timeout, retry, response-size, concurrency, and
minimum three-second arXiv request-spacing policies. It does not download paper
documents or invoke a model.

## Discovery control plane

M2.3 exposes the same bounded discovery operations through local CLI commands
and `/api/discovery` endpoints. From `backend/`:

```powershell
uv run python -m app.discovery.cli list-sources
uv run python -m app.discovery.cli source-health arxiv
uv run python -m app.discovery.cli sync arxiv --maximum-records 5 --lookback-hours 168
uv run python -m app.discovery.cli show-run <run-id>
```

The corresponding API operations are `GET /api/discovery/sources`, `GET
/api/discovery/sources/{source_key}/health`, `POST /api/discovery/sync`, and
`GET /api/discovery/runs/{run_id}`. Sync requests are synchronous and bounded
to one arXiv page, 1–25 records, and a 1–168 hour lookback. They retain all M2.1
HTTP, provenance, concurrency, and persistence limits.

## Catalog and Explore

The first visible research slice reads normalized papers from SQLite through:

- `GET /items` — paginated FTS keyword search with topic, source, publication
  date, and deterministic sort filters.
- `GET /items/{paper_id}` — current version, ordered authors, controlled topics,
  normalized identities, source, and canonical external link.
- `GET /catalog/filters` — populated topic and source filter options.
- `POST /sources/arxiv/sync` — the existing one-page bounded discovery service.

Open `http://127.0.0.1:5173/#explore` after starting the backend and frontend.
The UI renders only API text, allowlists canonical arXiv/DOI HTTPS links, and
never receives raw payloads or local filesystem paths.

Create a consistent online backup from the repository root after the database
has been initialized:

```powershell
.\scripts\backup-database.ps1
```

The default destination is a timestamped `.db` file under `data/backups`. A
custom destination must remain inside the configured data root:

```powershell
.\scripts\backup-database.ps1 --destination backups\manual.db
```

The backup command uses SQLite's online backup API, verifies integrity, writes
through a temporary file, and atomically renames the completed backup.

Credentials such as `AIOS_SOURCES__GITHUB_TOKEN` are optional, environment-only
`SecretStr` values. Their representation is redacted; do not place credentials
in committed files or logs.

## Hard resource policy

Configuration validation cannot be relaxed beyond these laptop ceilings:

- Non-LLM application RAM: 2,048 MB.
- Normal total project RAM during local-model work: 6,144 MB.
- Absolute temporary project peak: 8,192 MB.
- RAM reserved for Windows and other work: at least 8,192 MB.
- VRAM target: 6,656 MB (6.5 GiB), leaving GPU headroom.
- Source downloads: at most three concurrently.
- LLM generations: exactly one concurrently.
- Model profiles are on-demand, use zero keep-alive, and must unload afterward.

These values are configuration policy only in M0.2. No model is downloaded,
loaded, or called, and no resource-monitoring runtime is implemented yet.
