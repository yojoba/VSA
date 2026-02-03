#!/usr/bin/env bash
set -Eeuo pipefail

# migrate_electroziles.sh
# Migrate electroziles.flowbiz.ai to electroziles.ch + electroziles.com (with www variants)
# This script handles: htpasswd migration, old config removal, SAN certificate generation, NGINX reload

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
OLD_DOMAIN="electroziles.flowbiz.ai"
NEW_DOMAINS=("electroziles.ch" "www.electroziles.ch" "electroziles.com" "www.electroziles.com")
CERT_DOMAIN="electroziles.ch"

echo "[1/5] Copying HTTP Basic Auth file to new name"
OLD_HTPASSWD="/srv/flowbiz/reverse-proxy/nginx/auth/${OLD_DOMAIN}.htpasswd"
NEW_HTPASSWD="/srv/flowbiz/reverse-proxy/nginx/auth/electroziles.htpasswd"
if [[ -f "$OLD_HTPASSWD" ]]; then
  sudo cp "$OLD_HTPASSWD" "$NEW_HTPASSWD"
  echo "  ✅ Copied $OLD_HTPASSWD -> $NEW_HTPASSWD"
else
  echo "  ⚠️  Old htpasswd file not found, creating new one (will need to set credentials)"
  sudo mkdir -p "$(dirname "$NEW_HTPASSWD")"
  # Create empty file - user can add auth later with make add-basic-auth
  sudo touch "$NEW_HTPASSWD"
fi

echo "[2/6] Removing old vhost configuration"
OLD_VHOST_REPO="$STACK_DIR/nginx/conf.d/${OLD_DOMAIN}.conf"
OLD_VHOST_MOUNT="/srv/flowbiz/reverse-proxy/nginx/conf.d/${OLD_DOMAIN}.conf"
if [[ -f "$OLD_VHOST_REPO" ]]; then
  rm -f "$OLD_VHOST_REPO"
  echo "  ✅ Removed $OLD_VHOST_REPO"
fi
if [[ -f "$OLD_VHOST_MOUNT" ]]; then
  sudo rm -f "$OLD_VHOST_MOUNT"
  echo "  ✅ Removed $OLD_VHOST_MOUNT"
fi

echo "[3/6] Creating temporary HTTP-only vhost for ACME challenge"
NEW_VHOST="$STACK_DIR/nginx/conf.d/${CERT_DOMAIN}.conf"
# Backup the full HTTPS config if it exists
if [[ -f "$NEW_VHOST" ]]; then
  mv "$NEW_VHOST" "${NEW_VHOST}.https.backup"
  echo "  ✅ Backed up full HTTPS config"
fi
# Create temporary HTTP-only config
cat >"$NEW_VHOST" <<'NGINX_TEMP'
server {
  listen 80;
  server_name DOMAIN_PLACEHOLDER;
  location /.well-known/acme-challenge/ { root /var/www/certbot; }
  location / { return 301 https://$host$request_uri; }
}
NGINX_TEMP
sed -i "s/DOMAIN_PLACEHOLDER/${NEW_DOMAINS[0]} ${NEW_DOMAINS[1]} ${NEW_DOMAINS[2]} ${NEW_DOMAINS[3]}/g" "$NEW_VHOST"
echo "  ✅ Created temporary HTTP-only config"

echo "[4/6] Syncing vhost files from repo (HTTP-only for now)"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
"$SCRIPT_DIR/sync_vhosts.sh" || {
  # Fallback if sync_vhosts.sh doesn't exist or fails
  sudo rsync -av --delete "$STACK_DIR/nginx/conf.d/" "/srv/flowbiz/reverse-proxy/nginx/conf.d/"
  cd "$STACK_DIR"
  docker compose exec nginx nginx -s reload || true
}

echo "[5/6] Generating SAN certificate for all domains"
cd "$STACK_DIR"
docker compose run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
  -d "$CERT_DOMAIN" \
  -d "www.electroziles.ch" \
  -d "electroziles.com" \
  -d "www.electroziles.com" \
  --email ops@flowbiz.ai --agree-tos --no-eff-email

echo "[6/6] Restoring full HTTPS configuration and reloading NGINX"
# Restore the full HTTPS config
if [[ -f "${NEW_VHOST}.https.backup" ]]; then
  mv "${NEW_VHOST}.https.backup" "$NEW_VHOST"
  echo "  ✅ Restored full HTTPS config"
fi
# Sync final config
"$SCRIPT_DIR/sync_vhosts.sh" || {
  sudo rsync -av --delete "$STACK_DIR/nginx/conf.d/" "/srv/flowbiz/reverse-proxy/nginx/conf.d/"
  cd "$STACK_DIR"
  docker compose exec nginx nginx -s reload || true
}

echo ""
echo "✅ Migration complete!"
echo "  - Old domain: $OLD_DOMAIN (removed)"
echo "  - New domains: ${NEW_DOMAINS[*]}"
echo "  - Certificate: $CERT_DOMAIN (SAN covering all 4 domains)"
echo "  - Container: electroziles-frontend:3000"
echo "  - Auth: $NEW_HTPASSWD"

