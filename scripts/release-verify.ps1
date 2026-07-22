$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $root ".cache\uv"
$env:npm_config_cache = Join-Path $root ".cache\npm"
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"

& (Join-Path $PSScriptRoot "check.ps1")
if ($LASTEXITCODE -ne 0) { throw "Root quality checks failed." }

Push-Location (Join-Path $root "frontend")
try {
    npm run test:e2e
    if ($LASTEXITCODE -ne 0) { throw "Installed-Chrome tests failed." }
    npm audit --audit-level=high
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency audit failed." }
}
finally { Pop-Location }

Push-Location (Join-Path $root "backend")
try {
    uvx pip-audit --path .venv\Lib\site-packages
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency audit failed." }
}
finally { Pop-Location }

Write-Output "AI Intelligence OS V1 release verification: PASS"
