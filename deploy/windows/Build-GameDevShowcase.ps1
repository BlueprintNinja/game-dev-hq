[CmdletBinding()]
param([switch]$Check)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$logDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'GameDevHQ/logs'))
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$log = Join-Path $logDirectory 'showcase-build.log'
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
    Write-Log 'Building the sample showcase'
    Invoke-Native -File 'uv' -Arguments @('sync', '--locked', '--no-default-groups', '--group', 'browser') -Failure 'Pinned dependency setup failed'
    $env:DJANGO_SETTINGS_MODULE = 'hq.config.settings'
    $env:DJANGO_DEBUG = '1'
    $env:DJANGO_BEHIND_TLS_PROXY = '0'
    # A throwaway key for the export only; the static GetBytes overload is not
    # in the .NET Framework that Windows PowerShell runs on.
    $secretBytes = New-Object byte[] 48
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($secretBytes)
    }
    finally {
        $generator.Dispose()
    }
    $env:DJANGO_SECRET_KEY = [Convert]::ToBase64String($secretBytes)
    $env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost,testserver'
    $env:SEVERINO_DATABASE_PATH = Join-Path $repositoryRoot 'var/demo-build.sqlite3'
    $env:HQ_DEMO_OUT = Join-Path $repositoryRoot 'var/demo'
    $env:SEVERINO_HQ_PLUGINS = ''
    Invoke-Native -File 'uv' -Arguments @('run', '--no-sync', 'python', 'manage.py', 'test', 'demo.build', '--noinput', '--parallel', '1') -Failure 'Showcase export failed'
    if ($Check) {
        Invoke-Native -File 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'playwright', 'install', 'webkit') -Failure 'WebKit setup failed'
        Invoke-Native -File 'uv' -Arguments @('run', '--no-sync', 'python', '-m', 'unittest', 'demo.check', '-v') -Failure 'Showcase verification failed'
    }
    Write-Log 'The showcase was built'
} catch {
    Write-Log ("Showcase build failed: {0}" -f $_.Exception.Message)
    throw
} finally { Pop-Location }
