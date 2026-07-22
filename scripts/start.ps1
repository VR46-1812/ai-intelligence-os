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
$backendOutLog = Join-Path $cache "backend.out.log"
$backendErrorLog = Join-Path $cache "backend.err.log"
$frontendOutLog = Join-Path $cache "frontend.out.log"
$frontendErrorLog = Join-Path $cache "frontend.err.log"
try {
    foreach ($port in @(8000, 5173)) {
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($null -ne $listener) {
            throw "Port $port is already in use. Stop the existing local process before startup."
        }
    }
    $backend = Start-Process `
        -FilePath (Join-Path $root "backend\.venv\Scripts\python.exe") `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput $backendOutLog `
        -RedirectStandardError $backendErrorLog `
        -WindowStyle Hidden `
        -PassThru
    $frontend = Start-Process `
        -FilePath "node.exe" `
        -ArgumentList @((Join-Path $root "frontend\node_modules\vite\bin\vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
        -WorkingDirectory (Join-Path $root "frontend") `
        -RedirectStandardOutput $frontendOutLog `
        -RedirectStandardError $frontendErrorLog `
        -WindowStyle Hidden `
        -PassThru
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
        $failedService = "local services"
        if ($null -ne $backend -and $backend.HasExited) { $failedService = "backend" }
        elseif ($null -ne $frontend -and $frontend.HasExited) { $failedService = "frontend" }
        Write-Host "Startup failure: $failedService did not become ready." -ForegroundColor Red
        if (Test-Path -LiteralPath $backendErrorLog) {
            Write-Host "Backend error log tail ($backendErrorLog):" -ForegroundColor Yellow
            Get-Content -LiteralPath $backendErrorLog -Tail 40 | Write-Host
        }
        if ($failedService -eq "frontend" -and (Test-Path -LiteralPath $frontendErrorLog)) {
            Write-Host "Frontend error log tail ($frontendErrorLog):" -ForegroundColor Yellow
            Get-Content -LiteralPath $frontendErrorLog -Tail 40 | Write-Host
        }
        throw "$failedService startup failed. See the log tail above."
    }
    $backendListener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop |
        Select-Object -First 1
    $frontendListener = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction Stop |
        Select-Object -First 1
    @{ backend = $backendListener.OwningProcess; frontend = $frontendListener.OwningProcess } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $cache "local-app-pids.json")
    Write-Output "AI Intelligence OS started."
    Write-Output "UI: http://127.0.0.1:5173"
    Write-Output "API: http://127.0.0.1:8000/docs"
}
catch {
    if ($null -ne $frontend -and -not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force }
    if ($null -ne $backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
    throw
}
