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
$status = Invoke-Native -File 'tailscale' -Arguments @('status', '--json') -Failure 'Cannot read Tailscale status' | ConvertFrom-Json
if ($status.BackendState -ne 'Running') { throw 'Sign in to Tailscale first' }
$config = Invoke-Native -File 'tailscale' -Arguments @('serve', 'status', '--json') -Failure 'Cannot inspect Serve configuration' | ConvertFrom-Json
if ($config.AllowFunnel -and ($config.AllowFunnel.PSObject.Properties.Value -contains $true)) {
    throw 'Disable Funnel before enabling the private workspace'
}
$dnsName = $status.Self.DNSName.TrimEnd('.')
$old = $config.Web."${dnsName}:9443".Handlers.'/'.Proxy
if ($config.TCP.'9443' -and $old -notin @('http://127.0.0.1:18765', 'http://127.0.0.1:18767')) {
    throw '9443 is not a recognized Game Dev endpoint; refusing to replace it'
}
$demo = $config.Web."${dnsName}:9444".Handlers.'/'.Proxy
if ($config.TCP.'9444' -and $demo -ne 'http://127.0.0.1:18766') {
    throw '9444 is already used by another service'
}
$null = Invoke-WebRequest 'http://127.0.0.1:18767/health/live/' -UseBasicParsing
$null = Invoke-WebRequest 'http://127.0.0.1:18766/healthz' -UseBasicParsing
Invoke-Native -File 'tailscale' -Arguments @('serve', '--bg', '--https=9443', 'http://127.0.0.1:18767') -Failure 'Workspace route failed' | Out-Null
Invoke-Native -File 'tailscale' -Arguments @('serve', '--bg', '--https=9444', 'http://127.0.0.1:18766') -Failure 'Showcase route failed' | Out-Null
Write-Log "Routed 9443 to the workspace and 9444 to the showcase on ${dnsName}"
Write-Output "Live workspace: https://${dnsName}:9443/"
Write-Output "Sample showcase: https://${dnsName}:9444/"
