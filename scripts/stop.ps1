$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root ".cache\local-app-pids.json"
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "No workspace-managed local services are recorded."
    exit 0
}
$processes = Get-Content -LiteralPath $pidFile | ConvertFrom-Json
foreach ($processId in @($processes.frontend, $processes.backend)) {
    if ($null -ne $processId -and $null -ne (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $processId -Force
    }
}
Remove-Item -LiteralPath $pidFile -Force
Write-Output "AI Intelligence OS stopped."
