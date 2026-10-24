[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigDirectory,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$config = (Resolve-Path -LiteralPath $ConfigDirectory).Path
$python = (Resolve-Path -LiteralPath $PythonPath).Path
$data = (Resolve-Path -LiteralPath $DataDirectory).Path
$uv = (Get-Command $UvPath -ErrorAction Stop).Source
$shell = (Get-Command powershell.exe -ErrorAction Stop).Source
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($entry in @(@{Name='GameDevHQ-Phoenix';Script='Start-GameDevPhoenix.ps1';Config='server.json'},@{Name='GameDevHQ-Telemetry';Script='Start-GameDevTelemetry.ps1';Config='collector.json'})) {
    $file = Join-Path $PSScriptRoot $entry.Script
    $configuration = (Resolve-Path -LiteralPath (Join-Path $config $entry.Config)).Path
    $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -ConfigPath "{1}" -LogDirectory "{2}"' -f $file,$configuration,$LogDirectory
    if ($entry.Name -eq 'GameDevHQ-Phoenix') { $arguments += ' -PythonPath "{0}"' -f $python }
    else { $arguments += ' -DataDirectory "{0}" -UvPath "{1}"' -f $data,$uv }
    $existing = Get-ScheduledTask -TaskName $entry.Name -ErrorAction SilentlyContinue
    if ($existing -and $existing.Actions.Arguments -notmatch [regex]::Escape($entry.Script)) { throw "Refusing to replace unfamiliar task $($entry.Name)" }
    $action = New-ScheduledTaskAction -Execute $shell -Argument $arguments -WorkingDirectory $root
    Register-ScheduledTask -TaskName $entry.Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Output "Installed $($entry.Name) for the current operator."
}
