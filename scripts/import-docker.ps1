# Import enamad.sql.gz into Docker MariaDB and start services.
# Run from repo root:
#   .\scripts\import-docker.ps1
#
# Requires: Docker Desktop, enamad.sql.gz in repo root

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path "enamad.sql.gz")) {
    Write-Error "enamad.sql.gz not found in repo root"
}

Write-Host "=== 1) Start MySQL container ==="
docker compose up -d mysql

Write-Host "Waiting for MySQL healthy..."
$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 3
    $healthy = docker inspect --format '{{.State.Health.Status}}' enamad-mysql-1 2>$null
    if ($healthy -eq "healthy") { break }
} while ((Get-Date) -lt $deadline)
if ($healthy -ne "healthy") { throw "MySQL did not become healthy" }
Write-Host "MySQL is healthy."

Write-Host "=== 2) Import dump ==="
python scripts/import-docker.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "=== 3) Start web panel (Docker, local override) ==="
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build web
Write-Host ""
Write-Host "Web panel: http://127.0.0.1:8095/"
Write-Host "MySQL from host: 127.0.0.1:3307 (user root, password from .env)"
