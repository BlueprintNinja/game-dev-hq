[CmdletBinding()]
param([switch]$KeepRoutes)
$ErrorActionPreference = 'Stop'
# Stop the scheduled tasks and the loopback listeners they own. Nothing else is
# touched: the Tailscale routes stay unless -KeepRoutes is not given.
foreach ($name in @('GameDevHQ-Workspace', 'GameDevHQ-Showcase')) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Write-Output "Stopped task $name"
    }
}
$signatures = 'tools\.windows_hq\.asgi', 'gamedev serve', '\buv(\.exe)?\b.*(uvicorn|gamedev)'
foreach ($port in @(18766, 18767)) {
    foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
        $owner = $connection.OwningProcess
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        $command = [string]$process.CommandLine
        if (-not ($signatures | Where-Object { $command -match $_ })) {
            Write-Warning "Port $port is held by another program (PID $owner); leaving it alone"
            continue
        }
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped the Game Dev listener on port $port (PID $owner)"
    }
}
if (-not $KeepRoutes) {
    Write-Output 'The Tailscale routes on 9443 and 9444 still point at these endpoints.'
    Write-Output 'Run Enable-GameDevWorkspace.ps1 to re-point them after a move.'
}
