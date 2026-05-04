#!/usr/bin/env bash
set -Eeuo pipefail

echo "[bootstrap] Update apt and install prerequisites"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw fail2ban jq wget

echo "[bootstrap] Install Docker Engine and Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
# Use SUDO_USER when invoked via `sudo bash`, otherwise USER. The plain
# $USER resolves to "root" inside a sudo invocation, which would silently
# add root to the docker group (no-op) instead of the calling user.
TARGET_USER="${SUDO_USER:-$USER}"
sudo usermod -aG docker "$TARGET_USER" || true

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

echo "[bootstrap] Create VSA agent state + audit log directories"
# Without these, the first interactive `vsa` invocation by $TARGET_USER fails
# with `OperationalError: unable to open database file` because the systemd
# service (run as root) hasn't created them yet, OR has created them
# root-owned — interactive use would then crash on first audit write.
sudo mkdir -p /var/log/vsa /var/lib/vsa
sudo chown -R "$TARGET_USER:$TARGET_USER" /var/log/vsa /var/lib/vsa

echo "[bootstrap] Done. You may need to log out/in for docker group to take effect."


