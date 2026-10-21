[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$logDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GameDevHQ/logs'))
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log = Join-Path $logDirectory 'routes.log'
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
        $output = & $File @Arguments 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    $output | Out-File -LiteralPath $log -Append -Encoding utf8
    if ($code -ne 0) { throw $Failure }
    return $output
}
# Reserve a separate HTTPS port; never replace an existing Serve service.
$status = Invoke-Native -File 'tailscale' -Arguments @('status', '--json') -Failure 'Cannot read Tailscale status' | ConvertFrom-Json
if ($status.BackendState -ne 'Running') { throw 'Sign in to Tailscale first' }
$serve = Invoke-Native -File 'tailscale' -Arguments @('serve', 'status', '--json') -Failure 'Cannot inspect existing Serve configuration' | ConvertFrom-Json
if ($serve.TCP.'9443') { throw 'Port 9443 already has a Serve configuration; inspect it first' }
if ($serve.AllowFunnel -and ($serve.AllowFunnel.PSObject.Properties.Value -contains $true)) {
    throw 'Funnel is enabled on this machine; disable public exposure before continuing'
}
$health = Invoke-WebRequest -Uri 'http://127.0.0.1:18765/healthz' -UseBasicParsing
if ($health.Content.Trim() -ne 'ok') { throw 'Start the Game Dev preview first' }
Invoke-Native -File 'tailscale' -Arguments @('serve', '--bg', '--https=9443', 'http://127.0.0.1:18765') -Failure 'Tailscale Serve failed; check HTTPS approval in the admin console' | Out-Null
$dnsName = $status.Self.DNSName.TrimEnd('.')
Write-Log "Routed 9443 to the standalone preview on ${dnsName}"
Write-Output "Private preview: https://${dnsName}:9443/"
Write-Output 'Restrict tailnet grants/ACLs to your operator devices and TCP 9443. Never enable Funnel.'
