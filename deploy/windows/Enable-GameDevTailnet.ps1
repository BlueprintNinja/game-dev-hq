[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
# Reserve a separate HTTPS port; never replace an existing Serve service.
$statusText = & tailscale status --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read Tailscale status' }
$status = $statusText | ConvertFrom-Json
if ($status.BackendState -ne 'Running') { throw 'Sign in to Tailscale first' }
$serveText = & tailscale serve status --json
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing Serve configuration' }
$serve = $serveText | ConvertFrom-Json
if ($serve.TCP.'9443') { throw 'Port 9443 already has a Serve configuration; inspect it first' }
if ($serve.AllowFunnel -and ($serve.AllowFunnel.PSObject.Properties.Value -contains $true)) {
    throw 'Funnel is enabled on this machine; disable public exposure before continuing'
}
$health = Invoke-WebRequest -Uri 'http://127.0.0.1:18765/healthz' -UseBasicParsing
if ($health.Content.Trim() -ne 'ok') { throw 'Start the Game Dev preview first' }
& tailscale serve --bg --https=9443 http://127.0.0.1:18765
if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve failed; check HTTPS approval in the admin console' }
$dnsName = $status.Self.DNSName.TrimEnd('.')
Write-Output "Private preview: https://${dnsName}:9443/"
Write-Output 'Restrict tailnet grants/ACLs to your operator devices and TCP 9443. Never enable Funnel.'
