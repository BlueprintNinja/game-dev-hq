[CmdletBinding()]
param(
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs"
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$operatorAccount = [Security.Principal.WindowsIdentity]::GetCurrent().Name
# PowerShell 7 when the workstation has it, Windows PowerShell otherwise: the
# task must not depend on a runtime that an application may replace.
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$shellPath = if ($pwsh) { $pwsh.Source } else { (Get-Command powershell -ErrorAction Stop).Source }
$uvPath = (Get-Command uv -ErrorAction Stop).Source
# The paths are fixed here, not read from the task's environment at run time:
# a packaged host redirects LOCALAPPDATA, and the workspace must not follow it.
$dataDirectory = [IO.Path]::GetFullPath($DataDirectory)
$logDirectory = [IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$principal = New-ScheduledTaskPrincipal -UserId $operatorAccount -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $operatorAccount
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($entry in @(@{Name='GameDevHQ-Workspace'; Script='Start-GameDevWorkspace.ps1'; Data=$dataDirectory}, @{Name='GameDevHQ-Showcase'; Script='Start-GameDevShowcase.ps1'; Data=$null})) {
    $scriptPath = Join-Path $PSScriptRoot $entry.Script
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -UvPath "{1}" -LogDirectory "{2}"' -f $scriptPath, $uvPath, $logDirectory
    if ($entry.Data) { $arguments += ' -DataDirectory "{0}"' -f $entry.Data }
    $existing = Get-ScheduledTask -TaskName $entry.Name -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions.Arguments -notcontains $arguments)) {
        Write-Output "Replacing $($entry.Name); its previous action is recorded by Task Scheduler history"
    }
    $action = New-ScheduledTaskAction -Execute $shellPath -Argument $arguments -WorkingDirectory $repositoryRoot
    Register-ScheduledTask -TaskName $entry.Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Output "Installed $($entry.Name) for the current user's logon"
}
Write-Output "Workspace data: $dataDirectory"
Write-Output "Logs: $logDirectory"
