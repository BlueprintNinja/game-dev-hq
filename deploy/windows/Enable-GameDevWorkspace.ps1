[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$configText = & tailscale serve status --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Serve configuration' }
$config = $configText | ConvertFrom-Json
if ($config.AllowFunnel -and ($config.AllowFunnel.PSObject.Properties.Value -contains $true)) {
    throw 'Disable Funnel before enabling the private workspace'
}
$statusText = & tailscale status --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read Tailscale status' }
$status = $statusText | ConvertFrom-Json
if ($status.BackendState -ne 'Running') { throw 'Sign in to Tailscale first' }
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
& tailscale serve --bg --https=9443 http://127.0.0.1:18767
if ($LASTEXITCODE -ne 0) { throw 'Workspace route failed' }
& tailscale serve --bg --https=9444 http://127.0.0.1:18766
if ($LASTEXITCODE -ne 0) { throw 'Showcase route failed' }
Write-Output "Live workspace: https://${dnsName}:9443/"
Write-Output "Sample showcase: https://${dnsName}:9444/"
