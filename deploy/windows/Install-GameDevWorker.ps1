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
$data = (Resolve-Path -LiteralPath $DataDirectory).Path
$logs = [IO.Path]::GetFullPath($LogDirectory)
$uv = (Get-Command $UvPath -ErrorAction Stop).Source
$shell = (Get-Command powershell.exe -ErrorAction Stop).Source
$script = Join-Path $PSScriptRoot 'Start-GameDevWorker.ps1'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -PolicyPath "{1}" -DataDirectory "{2}" -LogDirectory "{3}" -UvPath "{4}" -WorkspacePort {5} -WorkspaceTimeoutSeconds {6}' -f $script, $policy, $data, $logs, $uv, $WorkspacePort, $WorkspaceTimeoutSeconds
$existing = Get-ScheduledTask -TaskName 'GameDevHQ-Worker' -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -notmatch 'Start-GameDevWorker\.ps1') { throw 'Refusing to replace an unfamiliar GameDevHQ-Worker task' }
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$action = New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $repositoryRoot
Register-ScheduledTask -TaskName 'GameDevHQ-Worker' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'Installed GameDevHQ-Worker for the current operator. The worker waits for the workspace before it opens the database.'
