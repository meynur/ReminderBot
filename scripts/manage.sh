#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update BOT_TOKEN, BOT_USERNAME and ADMIN_USER_ID."
fi

command="${1:-help}"
service="${2:-}"

case "$command" in
  start)
    docker compose up -d --build
    ;;
  stop)
    docker compose down
    ;;
  restart)
    docker compose down
    docker compose up -d --build
    ;;
  logs)
    if [[ -n "$service" ]]; then
      docker compose logs -f "$service"
    else
      docker compose logs -f backend caddy db
    fi
    ;;
  status)
    docker compose ps
    ;;
  panel)
    url="$(grep '^PUBLIC_BASE_URL=' .env | cut -d= -f2-)"
    echo "Panel: ${url:-http://localhost}"
    ;;
  help|*)
    cat <<'EOF'
Usage: ./scripts/manage.sh <command>

Commands:
  start
  stop
  restart
  logs [backend|db|caddy]
  status
  panel
EOF
    ;;
esac

