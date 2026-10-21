[CmdletBinding()]
param(
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$logDirectory = [IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log = Join-Path $logDirectory 'showcase.log'
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
$dnsName = $status.Self.DNSName.TrimEnd('.')
$operatorLogin = $status.User.($status.Self.UserID.ToString()).LoginName
Push-Location $repositoryRoot
try {
    Write-Log "Starting the Game Dev HQ showcase from $repositoryRoot"
    Invoke-Native -File $UvPath -Arguments @('sync', '--locked', '--no-default-groups') -Failure 'Pinned dependency setup failed'
    Invoke-Native -File $UvPath -Arguments @('run', '--no-sync', 'python', '-m', 'gamedev', 'init') -Failure 'Companion initialization failed'
    if (-not (Test-Path (Join-Path $repositoryRoot 'var/demo/index.html'))) {
        throw 'The sample showcase is not built; run deploy/windows/Build-GameDevShowcase.ps1 first'
    }
    Write-Log 'Showcase listening on 127.0.0.1:18766'
    Invoke-Native -File $UvPath -Arguments @('run', '--no-sync', 'python', '-m', 'gamedev', 'serve', '--site', 'var/demo', '--port', '18766', '--https-port', '9444', '--hostname', $dnsName, '--operator', $operatorLogin) -Failure 'Showcase stopped with an error'
    Write-Log 'Showcase stopped cleanly'
} catch {
    Write-Log ("Showcase failed: {0}" -f $_.Exception.Message)
    throw
} finally { Pop-Location }
