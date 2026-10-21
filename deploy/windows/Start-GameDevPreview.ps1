[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OperatorLogin,
    [Parameter(Mandatory)][string]$DnsName,
    [string]$Python = 'python',
    [string]$Database = "$env:LOCALAPPDATA\GameDevHQ\hq.sqlite3"
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $repositoryRoot
try {
    & $Python -m gamedev --database $Database init
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed' }
    & $Python -m gamedev --database $Database serve --hostname $DnsName --operator $OperatorLogin
    if ($LASTEXITCODE -ne 0) { throw 'Preview stopped with an error' }
} finally { Pop-Location }
