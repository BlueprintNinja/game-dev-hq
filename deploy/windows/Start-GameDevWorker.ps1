[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PolicyPath,
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [string]$UvPath = 'uv',
    [int]$WorkspacePort = 18767,
    [int]$WorkspaceTimeoutSeconds = 600
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$policy = (Resolve-Path -LiteralPath $PolicyPath).Path
$env:GAMEDEV_WORKSPACE_DATA = (Resolve-Path -LiteralPath $DataDirectory).Path
$status = (& tailscale status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $status.BackendState -ne 'Running') { throw 'Tailscale must be running before the worker starts' }
$env:GAMEDEV_WORKSPACE_HOST = $status.Self.DNSName.TrimEnd('.')
$env:GAMEDEV_OPERATOR_LOGIN = $status.User.($status.Self.UserID.ToString()).LoginName
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$log = Join-Path $LogDirectory 'worker.log'
$health = "http://127.0.0.1:$WorkspacePort/health/live/"
Push-Location $repositoryRoot
try {
    # Dependencies and schema are deployed by the workspace first. Never race its
    # initialization: the worker waits for the workspace to answer before it opens
    # the database, so a logon that starts both at once cannot read a
    # half-migrated file.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $UvPath run --no-sync python -m gamedev.worker --policy $policy --workspace-url $health --workspace-timeout $WorkspaceTimeoutSeconds *>> $log
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { throw "Worker stopped with exit code $code; inspect worker.log" }
} finally { Pop-Location }
