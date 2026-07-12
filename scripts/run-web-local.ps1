# Run Enamad admin web panel locally (Flask dev server).
# MySQL must already be running (docker compose up -d mysql) with data imported.
#
#   .\scripts\run-web-local.ps1
#
# Open: http://127.0.0.1:8095/

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
    }
}

# Minimal deps for web panel (no OCR / bot stack)
$deps = @("Flask>=3.0.0", "PyMySQL>=1.1.0", "requests>=2.31.0")
python -c "import flask, pymysql" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing web dependencies..."
    pip install @deps
}

# Load .env into process env (simple KEY=VALUE parser)
foreach ($line in Get-Content ".env" -ErrorAction SilentlyContinue) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $k, $v = $line -split '=', 2
    if ($k.Trim()) { Set-Item -Path "env:$($k.Trim())" -Value $v.Trim() }
}

if (-not $env:WEB_ADMIN_PASSWORD) {
    $env:WEB_ADMIN_PASSWORD = "localdev"
    Write-Host "WEB_ADMIN_PASSWORD not set - using localdev"
}

$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
if (-not $env:MYSQL_PASSWORD) { $env:MYSQL_PASSWORD = "" }
if (-not $env:MYSQL_DATABASE) { $env:MYSQL_DATABASE = "enamad" }
$env:WEB_PORT = "8095"

Write-Host "Starting web panel at http://127.0.0.1:8095/"
Write-Host ("Login: admin / password: " + $env:WEB_ADMIN_PASSWORD)
python webapp.py
