#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SUDO=""
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  SUDO="sudo"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  $SUDO apt-get update
  $SUDO apt-get install -y ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  source /etc/os-release
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    ${VERSION_CODENAME} stable" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from template. Update tokens and admin id before public use."
fi

chmod +x scripts/manage.sh

if grep -q '^PANEL_TOKEN=change-me-panel-token$' .env && command -v openssl >/dev/null 2>&1; then
  token="$(openssl rand -hex 16)"
  sed -i "s/^PANEL_TOKEN=.*/PANEL_TOKEN=${token}/" .env
  echo "Generated PANEL_TOKEN: ${token}"
fi

docker compose up -d --build

echo
echo "Ready. Useful commands:"
echo "  ./scripts/manage.sh status"
echo "  ./scripts/manage.sh logs backend"
echo "  ./scripts/manage.sh panel"

