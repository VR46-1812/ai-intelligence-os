$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"
$env:npm_config_cache = Join-Path $root ".cache\npm"

function Invoke-Checked {
    param([Parameter(Mandatory)] [scriptblock] $Command)

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE."
    }
}

Push-Location (Join-Path $root "backend")
try {
    Invoke-Checked { uv sync --frozen }
    Invoke-Checked { uv run pytest }
    Invoke-Checked { uv run ruff check . }
    Invoke-Checked { uv run ruff format --check . }
    Invoke-Checked { uv run pyright }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $root "frontend")
try {
    Invoke-Checked { npm ci }
    Invoke-Checked { npm run lint }
    Invoke-Checked { npm run build }
}
finally {
    Pop-Location
}
