$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $root ".cache"
$env:UV_CACHE_DIR = Join-Path $cache "uv"
$env:npm_config_cache = Join-Path $cache "npm"
New-Item -ItemType Directory -Force -Path $cache | Out-Null

Push-Location (Join-Path $root "backend")
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency synchronization failed." }
}
finally { Pop-Location }

Push-Location (Join-Path $root "frontend")
try {
    if (-not (Test-Path -LiteralPath "node_modules")) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    }
}
finally { Pop-Location }

$backend = $null
$frontend = $null
try {
    $backend = Start-Process `
        -FilePath (Join-Path $root "backend\.venv\Scripts\python.exe") `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput (Join-Path $cache "backend.out.log") `
        -RedirectStandardError (Join-Path $cache "backend.err.log") `
        -WindowStyle Hidden `
        -PassThru
    $frontend = Start-Process `
        -FilePath "node.exe" `
        -ArgumentList @((Join-Path $root "frontend\node_modules\vite\bin\vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
        -WorkingDirectory (Join-Path $root "frontend") `
        -RedirectStandardOutput (Join-Path $cache "frontend.out.log") `
        -RedirectStandardError (Join-Path $cache "frontend.err.log") `
        -WindowStyle Hidden `
        -PassThru
    @{ backend = $backend.Id; frontend = $frontend.Id } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $cache "local-app-pids.json")

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
            $ui = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1 -UseBasicParsing
            if ($health.status -eq "ok" -and $ui.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) {
        throw "Local services did not become ready. Check .cache backend/frontend logs."
    }
    Write-Output "AI Intelligence OS started."
    Write-Output "UI: http://127.0.0.1:5173"
    Write-Output "API: http://127.0.0.1:8000/docs"
}
catch {
    if ($null -ne $frontend -and -not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force }
    if ($null -ne $backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    throw
}
