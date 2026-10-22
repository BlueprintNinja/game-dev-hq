[CmdletBinding()]
param(
    [string]$DataDirectory = "$env:LOCALAPPDATA\GameDevHQ\workspace",
    [string]$BackupDirectory = "$env:LOCALAPPDATA\GameDevHQ\backups",
    [string]$LogDirectory = "$env:LOCALAPPDATA\GameDevHQ\logs",
    [int]$Keep = 14,
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$data = (Resolve-Path -LiteralPath $DataDirectory).Path
$backups = [IO.Path]::GetFullPath($BackupDirectory)
$logs = [IO.Path]::GetFullPath($LogDirectory)
New-Item -ItemType Directory -Force -Path $backups | Out-Null
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'backup.log'
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
    Write-Log "Backing up the workspace database in $data to $backups"
    Invoke-Native -File $UvPath -Arguments @('run', '--no-sync', 'python', '-m', 'gamedev.backup', '--data-directory', $data, '--backup-directory', $backups, '--keep', $Keep) -Failure 'The workspace backup failed'
    Write-Log 'Workspace backup written'
} catch {
    Write-Log ("Workspace backup failed: {0}" -f $_.Exception.Message)
    throw
} finally { Pop-Location }
