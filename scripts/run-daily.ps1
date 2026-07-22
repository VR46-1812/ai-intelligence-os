$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"
Push-Location (Join-Path $root "backend")
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
    uv run python -m app.operations.cli run-now
    if ($LASTEXITCODE -ne 0) { throw "The bounded daily run failed." }
}
finally { Pop-Location }
