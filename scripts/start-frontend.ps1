$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:npm_config_cache = Join-Path $root ".cache\npm"

Push-Location (Join-Path $root "frontend")
try {
    if (-not (Test-Path -LiteralPath "node_modules")) {
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed with exit code $LASTEXITCODE."
        }
    }
    npm run dev
}
finally {
    Pop-Location
}
