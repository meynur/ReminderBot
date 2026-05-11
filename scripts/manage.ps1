param(
    [Parameter(Position = 0)]
    [string]$Command = "help",
    [Parameter(Position = 1)]
    [string]$Service = ""
)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Update BOT_TOKEN, BOT_USERNAME and ADMIN_USER_ID." -ForegroundColor Yellow
}

switch ($Command) {
    "start" { docker compose up -d --build }
    "stop" { docker compose down }
    "restart" {
        docker compose down
        docker compose up -d --build
    }
    "logs" {
        if ($Service) {
            docker compose logs -f $Service
        } else {
            docker compose logs -f backend caddy db
        }
    }
    "status" { docker compose ps }
    "panel" {
        $line = Select-String -Path ".env" -Pattern "^PUBLIC_BASE_URL=" | Select-Object -First 1
        $url = if ($line) { $line.Line.Split("=", 2)[1] } else { "http://localhost" }
        Write-Host "Panel: $url"
    }
    Default {
        Write-Host "Usage: .\scripts\manage.ps1 <command>" -ForegroundColor Cyan
        Write-Host "Commands: start, stop, restart, logs, status, panel"
    }
}

