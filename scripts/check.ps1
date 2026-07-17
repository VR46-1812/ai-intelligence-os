$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"
$env:npm_config_cache = Join-Path $root ".cache\npm"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"

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
    Invoke-Checked { npm test -- --run }
    Invoke-Checked { npm run lint }
    Invoke-Checked { npm run build }
    Invoke-Checked { npm run test:e2e:list }
}
finally {
    Pop-Location
}

& (Join-Path $PSScriptRoot "smoke.ps1")
