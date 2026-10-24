[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs"
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$python = (Resolve-Path -LiteralPath $PythonPath).Path
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$log = Join-Path $LogDirectory 'phoenix.log'
$ErrorActionPreference = 'Continue'
& $python (Join-Path $root 'deploy/phoenix/server.py') --config $config *>> $log
if ($LASTEXITCODE -ne 0) { throw 'Phoenix stopped. Inspect phoenix.log.' }
