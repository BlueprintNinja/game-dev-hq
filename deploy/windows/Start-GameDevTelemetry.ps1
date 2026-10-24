[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$env:GAMEDEV_WORKSPACE_DATA = (Resolve-Path -LiteralPath $DataDirectory).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$status = (& tailscale status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $status.BackendState -ne 'Running') { throw 'Tailscale must be running' }
$env:GAMEDEV_WORKSPACE_HOST = $status.Self.DNSName.TrimEnd('.')
$env:GAMEDEV_OPERATOR_LOGIN = $status.User.($status.Self.UserID.ToString()).LoginName
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$log = Join-Path $LogDirectory 'telemetry.log'
Push-Location $root
try {
    $ErrorActionPreference = 'Continue'
    & $UvPath run --no-sync python -m gamedev.collector --config $config --workspace-url 'http://127.0.0.1:18767/health/live/' *>> $log
    if ($LASTEXITCODE -ne 0) { throw 'Telemetry collector stopped. Inspect telemetry.log.' }
} finally { Pop-Location }
