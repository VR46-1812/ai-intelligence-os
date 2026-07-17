$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"

Push-Location (Join-Path $root "backend")
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency synchronization failed with exit code $LASTEXITCODE."
    }
    uv run python -m app.db.backup @args
    if ($LASTEXITCODE -ne 0) {
        throw "Database backup failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
