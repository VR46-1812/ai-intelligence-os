$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"

Push-Location (Join-Path $root "backend")
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency synchronization failed with exit code $LASTEXITCODE."
    }
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
}
finally {
    Pop-Location
}
