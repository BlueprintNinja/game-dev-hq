[CmdletBinding()]
param(
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dataDirectory = [IO.Path]::GetFullPath($DataDirectory)
$logDirectory = [IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log = Join-Path $logDirectory 'workspace.log'
function Write-Log([string]$Message) {
    Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}
# Native tools write progress to stderr; that is output, not a failure. The exit
# code decides whether the step worked.
function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$Failure)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments *>> $log
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw $Failure }
}
# Tailscale may still be signing in at logon; wait for it instead of failing.
$status = $null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $statusText = & tailscale status --json 2>$null
    if ($LASTEXITCODE -eq 0) {
        $status = $statusText | ConvertFrom-Json
        if ($status.BackendState -eq 'Running') { break }
    }
    $status = $null
    Start-Sleep -Seconds 4
}
if (-not $status) { throw 'Tailscale did not reach a running state; sign in and start the task again' }
$env:GAMEDEV_WORKSPACE_DATA = $dataDirectory
$env:GAMEDEV_WORKSPACE_HOST = $status.Self.DNSName.TrimEnd('.')
$env:GAMEDEV_OPERATOR_LOGIN = $status.User.($status.Self.UserID.ToString()).LoginName
Push-Location $repositoryRoot
try {
    Write-Log "Starting the Game Dev HQ workspace from $repositoryRoot with data in $dataDirectory"
    Invoke-Native -File $UvPath -Arguments @('sync', '--locked', '--no-default-groups') -Failure 'Pinned dependency setup failed'
    Invoke-Native -File $UvPath -Arguments @('run', '--no-sync', 'python', '-m', 'tools.windows_hq.manage', 'initialize') -Failure 'Workspace initialization failed'
    Write-Log 'Workspace initialized; listening on 127.0.0.1:18767'
    Invoke-Native -File $UvPath -Arguments @('run', '--no-sync', 'python', '-m', 'uvicorn', 'tools.windows_hq.asgi:create_app', '--factory', '--host', '127.0.0.1', '--port', '18767', '--no-proxy-headers') -Failure 'Workspace stopped with an error'
    Write-Log 'Workspace stopped cleanly'
} catch {
    Write-Log ("Workspace failed: {0}" -f $_.Exception.Message)
    throw
} finally { Pop-Location }
