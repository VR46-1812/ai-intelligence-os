# Local development

M0.1 provides a FastAPI health endpoint and a React/Vite application shell. It
does not include persistence, connectors, local models, or analysis pipelines.

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
