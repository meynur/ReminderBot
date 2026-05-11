$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$dockerExists = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExists) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Host "Docker was not found and winget is unavailable. Install Docker Desktop manually first." -ForegroundColor Red
        exit 1
    }

    Write-Host "Installing Docker Desktop via winget..." -ForegroundColor Yellow
    winget install --id Docker.DockerDesktop -e --accept-source-agreements --accept-package-agreements
    Write-Host "Start Docker Desktop once after installation, then run this script again." -ForegroundColor Yellow
    exit 0
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Update BOT_TOKEN, BOT_USERNAME and ADMIN_USER_ID." -ForegroundColor Yellow
}

docker compose up -d --build

Write-Host ""
Write-Host "Ready. Useful commands:" -ForegroundColor Green
Write-Host "  .\scripts\manage.ps1 status"
Write-Host "  .\scripts\manage.ps1 logs backend"
Write-Host "  .\scripts\manage.ps1 panel"

