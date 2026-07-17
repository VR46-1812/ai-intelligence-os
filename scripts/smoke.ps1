$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $root ".cache"
New-Item -ItemType Directory -Force -Path $cache | Out-Null

$backend = $null
$frontend = $null

try {
    $backend = Start-Process `
        -FilePath (Join-Path $root "backend\.venv\Scripts\python.exe") `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput (Join-Path $cache "backend-smoke.out.log") `
        -RedirectStandardError (Join-Path $cache "backend-smoke.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $frontend = Start-Process `
        -FilePath "node.exe" `
        -ArgumentList @(
            (Join-Path $root "frontend\node_modules\vite\bin\vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "5173",
            "--strictPort"
        ) `
        -WorkingDirectory (Join-Path $root "frontend") `
        -RedirectStandardOutput (Join-Path $cache "frontend-smoke.out.log") `
        -RedirectStandardError (Join-Path $cache "frontend-smoke.err.log") `
        -WindowStyle Hidden `
        -PassThru

    $api = $null
    $ui = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $api = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
            $ui = Invoke-WebRequest `
                -Uri "http://127.0.0.1:5173" `
                -TimeoutSec 1 `
                -UseBasicParsing
            break
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }

    if ($null -eq $api -or $null -eq $ui) {
        throw "Local runtime smoke test did not become ready."
    }

    if ($api.status -ne "ok" -or $api.service -ne "ai-intelligence-os") {
        throw "The health endpoint returned an unexpected contract."
    }
    if ($ui.StatusCode -ne 200 -or -not $ui.Content.Contains("<title>AI Intelligence OS</title>")) {
        throw "The frontend did not return the expected application shell."
    }

    Write-Output "API health: PASS"
    Write-Output "Frontend response: PASS"
}
finally {
    if ($null -ne $frontend -and -not $frontend.HasExited) {
        Stop-Process -Id $frontend.Id -Force
    }
    if ($null -ne $backend -and -not $backend.HasExited) {
        Stop-Process -Id $backend.Id -Force
    }
}
