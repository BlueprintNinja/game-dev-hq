[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$logDirectory = Join-Path $env:LOCALAPPDATA 'GameDevHQ/logs'
foreach ($name in @('GameDevHQ-Workspace', 'GameDevHQ-Showcase')) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Output "$name is not installed"
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $name
    Write-Output ("{0}: {1}; last run {2}; last result {3}" -f $name, $task.State, $info.LastRunTime, $info.LastTaskResult)
}
foreach ($port in @(18766, 18767)) {
    $connection = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $connection) {
        Write-Output "Port $port is not listening"
        continue
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
    Write-Output ("Port {0} is served by PID {1} ({2})" -f $port, $connection.OwningProcess, $process.Name)
}
foreach ($probe in @(@{Url='http://127.0.0.1:18767/health/live/'; Name='Workspace health'}, @{Url='http://127.0.0.1:18766/healthz'; Name='Showcase health'})) {
    try {
        $response = Invoke-WebRequest $probe.Url -UseBasicParsing -TimeoutSec 10
        Write-Output ("{0}: HTTP {1} ({2})" -f $probe.Name, $response.StatusCode, $response.Content.Trim())
    } catch {
        Write-Output ("{0}: not answering ({1})" -f $probe.Name, $_.Exception.Message)
    }
}
$statusText = & tailscale status --json
if ($LASTEXITCODE -eq 0) {
    $status = $statusText | ConvertFrom-Json
    $dnsName = $status.Self.DNSName.TrimEnd('.')
    Write-Output "Live workspace: https://${dnsName}:9443/"
    Write-Output "Sample showcase: https://${dnsName}:9444/"
}
foreach ($log in @('workspace.log', 'showcase.log')) {
    $path = Join-Path $logDirectory $log
    if (-not (Test-Path $path)) { continue }
    Write-Output "Last lines of ${path}:"
    Get-Content $path -Tail 5 | ForEach-Object { "  $_" }
}
