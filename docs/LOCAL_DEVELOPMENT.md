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

Run a bounded local startup smoke test with:

```powershell
.\scripts\smoke.ps1
```

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

- Database parent directory (the database file is not created in M0.2).
- Raw and processed document directories.
- Quarantine and temporary directories.

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
