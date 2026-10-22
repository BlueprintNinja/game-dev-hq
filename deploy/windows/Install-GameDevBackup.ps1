[CmdletBinding()]
param(
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$BackupDirectory = "$env:LOCALAPPDATA\GameDevHQ\backups",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [int]$Keep = 14,
    [string]$At = '03:30',
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$data = (Resolve-Path -LiteralPath $DataDirectory).Path
$backups = [IO.Path]::GetFullPath($BackupDirectory)
$logs = [IO.Path]::GetFullPath($LogDirectory)
$uv = (Get-Command $UvPath -ErrorAction Stop).Source
$shell = (Get-Command powershell.exe -ErrorAction Stop).Source
$script = Join-Path $PSScriptRoot 'Backup-GameDevWorkspace.ps1'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -DataDirectory "{1}" -BackupDirectory "{2}" -LogDirectory "{3}" -Keep {4} -UvPath "{5}"' -f $script, $data, $backups, $logs, $Keep, $uv
$existing = Get-ScheduledTask -TaskName 'GameDevHQ-Backup' -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -notmatch 'Backup-GameDevWorkspace\.ps1') { throw 'Refusing to replace an unfamiliar GameDevHQ-Backup task' }
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$time = [datetime]::ParseExact($At, 'HH:mm', $null)
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$action = New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $repositoryRoot
Register-ScheduledTask -TaskName 'GameDevHQ-Backup' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "Installed GameDevHQ-Backup for the current operator, daily at $At, keeping $Keep backups in $backups."
