[CmdletBinding()]
param([switch]$Check)
$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $repositoryRoot
try {
    & uv sync --locked --no-default-groups --group browser
    if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency setup failed' }
    $env:DJANGO_SETTINGS_MODULE = 'hq.config.settings'
    $env:DJANGO_DEBUG = '1'
    $env:DJANGO_BEHIND_TLS_PROXY = '0'
    $env:DJANGO_SECRET_KEY = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
    $env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost,testserver'
    $env:SEVERINO_DATABASE_PATH = Join-Path $repositoryRoot 'var/demo-build.sqlite3'
    $env:HQ_DEMO_OUT = Join-Path $repositoryRoot 'var/demo'
    $env:SEVERINO_HQ_PLUGINS = ''
    & uv run --no-sync python manage.py test demo.build --noinput --parallel 1
    if ($LASTEXITCODE -ne 0) { throw 'Showcase export failed' }
    if ($Check) {
        & uv run --no-sync python -m playwright install webkit
        if ($LASTEXITCODE -ne 0) { throw 'WebKit setup failed' }
        & uv run --no-sync python -m unittest demo.check -v
        if ($LASTEXITCODE -ne 0) { throw 'Showcase verification failed' }
    }
} finally { Pop-Location }
