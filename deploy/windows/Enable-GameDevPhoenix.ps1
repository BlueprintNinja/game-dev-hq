[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$status = (& tailscale status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $status.BackendState -ne 'Running') { throw 'Tailscale must be running' }
$config = (& tailscale serve status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Serve configuration' }
if ($config.AllowFunnel -and ($config.AllowFunnel.PSObject.Properties.Value -contains $true)) { throw 'Disable Funnel before enabling Phoenix' }
$dns = $status.Self.DNSName.TrimEnd('.')
$old = $config.Web."${dns}:9445".Handlers.'/'.Proxy
if ($config.TCP.'9445' -and $old -ne 'http://127.0.0.1:6006') { throw '9445 belongs to an unrecognized service' }
# Authentication must reject anonymous project reads before a tailnet route exists.
$code = 0
try { $code = (Invoke-WebRequest 'http://127.0.0.1:6006/v1/projects' -UseBasicParsing).StatusCode }
catch { if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } else { throw } }
if ($code -ne 401) { throw 'Phoenix did not enforce authentication; refusing to create a route' }
& tailscale serve --bg --https=9445 http://127.0.0.1:6006
if ($LASTEXITCODE -ne 0) { throw 'Phoenix route failed' }
Write-Output "Private Phoenix: https://${dns}:9445/"
