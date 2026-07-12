# Pull Enamad MySQL dump from production server and import into local Docker MySQL.
# Run from repo root in PowerShell:
#   .\scripts\pull-db.ps1
#
# Prerequisites: Docker Desktop running, SSH access to server (ssh root@YOUR_SERVER_IP)
#
# Override the server per-run, e.g.:
#   .\scripts\pull-db.ps1 -Server root@203.0.113.10

param(
    [string]$Server = "root@YOUR_SERVER_IP",
    [string]$RemoteDir = "/srv/enamad",
    [string]$LocalSql = "enamad.sql",
    [string]$MysqlPassword = "",
    [string]$MysqlDatabase = "enamad"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — edit MYSQL_PASSWORD if needed."
}

# Read password from .env if present
foreach ($line in Get-Content ".env" -ErrorAction SilentlyContinue) {
    if ($line -match '^MYSQL_PASSWORD=(.+)$') { $MysqlPassword = $Matches[1].Trim() }
    if ($line -match '^MYSQL_DATABASE=(.+)$') { $MysqlDatabase = $Matches[1].Trim() }
}

Write-Host "=== 1) Export database on server ==="
$remoteCmd = @"
cd $RemoteDir && \
PW=`$(grep -E '^MYSQL_PASSWORD=' .env | head -1 | cut -d= -f2-) && \
DB=`$(grep -E '^MYSQL_DATABASE=' .env | head -1 | cut -d= -f2-); DB=`${DB:-enamad}; \
docker exec enamad-mysql-1 mariadb-dump -uroot -p"`$PW" --single-transaction --routines --triggers "`$DB"
"@
ssh $Server $remoteCmd | Set-Content -Encoding utf8 $LocalSql
$sizeMb = [math]::Round((Get-Item $LocalSql).Length / 1MB, 1)
Write-Host "Saved $LocalSql ($sizeMb MB)"

Write-Host "=== 2) Start local MySQL (Docker) ==="
docker compose up -d mysql
Write-Host "Waiting for MySQL to become healthy..."
$deadline = (Get-Date).AddMinutes(3)
do {
    Start-Sleep -Seconds 3
    $healthy = docker inspect --format '{{.State.Health.Status}}' enamad-mysql-1 2>$null
    if ($healthy -eq "healthy") { break }
} while ((Get-Date) -lt $deadline)
if ($healthy -ne "healthy") { throw "MySQL did not become healthy in time" }
Write-Host "MySQL is healthy."

Write-Host "=== 3) Import dump ==="
Get-Content $LocalSql -Raw | docker exec -i enamad-mysql-1 mariadb -uroot -p"$MysqlPassword" $MysqlDatabase
Write-Host "Import done."

Write-Host "=== 4) Quick count ==="
docker exec enamad-mysql-1 mariadb -uroot -p"$MysqlPassword" -e "SELECT COUNT(*) AS domains FROM enamad_domains;" $MysqlDatabase

Write-Host ""
Write-Host "Next: run the web panel locally:"
Write-Host "  .\scripts\run-web-local.ps1"
