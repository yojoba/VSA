#!/usr/bin/env bash
set -Eeuo pipefail

echo "[bootstrap] Update apt and install prerequisites"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw fail2ban jq wget

echo "[bootstrap] Install Docker Engine and Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
sudo usermod -aG docker "$USER" || true

if ! docker compose version >/dev/null 2>&1; then
  echo "[bootstrap] Docker Compose v2 is included with Docker; ensuring it's available"
fi

echo "[bootstrap] Configure UFW"
sudo ufw allow 22/tcp || true
sudo ufw allow 80/tcp || true
sudo ufw allow 443/tcp || true
sudo ufw --force enable || true

echo "[bootstrap] Ensure external Docker network 'flowbiz_ext' exists"
docker network create flowbiz_ext >/dev/null 2>&1 || true

echo "[bootstrap] Create base directories for reverse proxy"
sudo mkdir -p /srv/flowbiz/reverse-proxy/{letsencrypt,certbot-www,logs}
sudo chown -R root:root /srv/flowbiz/reverse-proxy

echo "[bootstrap] Done. You may need to log out/in for docker group to take effect."


