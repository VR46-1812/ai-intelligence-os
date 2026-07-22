$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"
Push-Location (Join-Path $root "backend")
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
    uv run python -m app.db.backup --restore-from @args
    if ($LASTEXITCODE -ne 0) { throw "Database restore failed." }
}
finally { Pop-Location }
