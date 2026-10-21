[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OperatorLogin,
    [Parameter(Mandatory)][string]$DnsName,
    [string]$Python = 'python',
    [string]$Database = "$env:LOCALAPPDATA\GameDevHQ\hq.sqlite3",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs"
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$logDirectory = [IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log = Join-Path $logDirectory 'preview.log'
function Write-Log([string]$Message) {
    Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}
# Native tools write progress to stderr; that is output, not a failure. The exit
# code decides whether the step worked.
function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$Failure)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $File @Arguments *>> $log
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw $Failure }
}
Push-Location $repositoryRoot
try {
    Write-Log "Starting the standalone preview with data in $Database"
    Invoke-Native -File $Python -Arguments @('-m', 'gamedev', '--database', $Database, 'init') -Failure 'Database initialization failed'
    Invoke-Native -File $Python -Arguments @('-m', 'gamedev', '--database', $Database, 'serve', '--hostname', $DnsName, '--operator', $OperatorLogin) -Failure 'Preview stopped with an error'
} catch {
    Write-Log ("Preview failed: {0}" -f $_.Exception.Message)
    throw
} finally { Pop-Location }
