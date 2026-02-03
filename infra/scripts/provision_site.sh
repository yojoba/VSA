#!/usr/bin/env bash
set -Eeuo pipefail

# provision_site.sh
# Automate: attach container to flowbiz_ext, create NGINX vhost, issue LE cert, reload
# Usage:
#   ./infra/scripts/provision_site.sh --domain <domain> --container <container_name> --port <internal_port> [--no-www]

usage() {
  echo "Usage: $0 --domain <domain> --container <container_name> --port <internal_port> [--no-www]" >&2
  exit 1
}

DOMAIN=""
CONTAINER=""
PORT=""
INCLUDE_WWW=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    --container) CONTAINER="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --no-www) INCLUDE_WWW=0; shift;;
    *) usage;;
  esac
done

if [[ -z "$DOMAIN" || -z "$CONTAINER" || -z "$PORT" ]]; then
  usage
fi

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
VHOST_SRC="$STACK_DIR/nginx/conf.d/${DOMAIN}.conf"
VHOST_DST="/srv/flowbiz/reverse-proxy/nginx/conf.d/${DOMAIN}.conf"

echo "[1/6] Ensure reverse-proxy mounts exist"
sudo mkdir -p /srv/flowbiz/reverse-proxy/{letsencrypt,certbot-www,logs} /srv/flowbiz/reverse-proxy/nginx/{conf.d,snippets}

echo "[2/6] Attach container '$CONTAINER' to flowbiz_ext (idempotent)"
if ! docker network inspect flowbiz_ext >/dev/null 2>&1; then
  docker network create flowbiz_ext >/dev/null
fi
if ! docker inspect -f '{{json .NetworkSettings.Networks}}' "$CONTAINER" 2>/dev/null | grep -q 'flowbiz_ext'; then
  docker network connect flowbiz_ext "$CONTAINER" || true
fi

echo "[3/6] Generate temporary HTTP-only vhost for ACME at $VHOST_SRC"
cat >"$VHOST_SRC" <<'NGINX'
server {
  listen 80;
  server_name DOMAIN_PLACEHOLDER WWW_DOMAIN_PLACEHOLDER;
  location /.well-known/acme-challenge/ { root /var/www/certbot; }
  return 301 https://$host$request_uri;
}
NGINX

# Replace placeholders
if [[ $INCLUDE_WWW -eq 1 ]]; then
  sed -i "s/DOMAIN_PLACEHOLDER/${DOMAIN}/g; s/WWW_DOMAIN_PLACEHOLDER/www.${DOMAIN}/g" "$VHOST_SRC"
else
  sed -i "s/DOMAIN_PLACEHOLDER/${DOMAIN}/g; s/WWW_DOMAIN_PLACEHOLDER//g" "$VHOST_SRC"
fi

echo "[4/6] Deploy HTTP-only vhost and reload NGINX"
sudo cp "$VHOST_SRC" "$VHOST_DST"
docker compose -f "$STACK_DIR/compose.yml" up -d --build
docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload || true

echo "[5/6] Issue Let's Encrypt certificate for ${DOMAIN}${INCLUDE_WWW:+ and www.${DOMAIN}}"
CERT_DOMAINS=(-d "$DOMAIN")
if [[ $INCLUDE_WWW -eq 1 ]]; then CERT_DOMAINS+=(-d "www.$DOMAIN"); fi
docker compose -f "$STACK_DIR/compose.yml" run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot "${CERT_DOMAINS[@]}" \
  --email ops@flowbiz.ai --agree-tos --no-eff-email

echo "[6/6] Generate final HTTPS vhost with upstream and reload"
cat >"$VHOST_SRC" <<'NGINX'
server {
  listen 80;
  server_name DOMAIN_PLACEHOLDER WWW_DOMAIN_PLACEHOLDER;
  location /.well-known/acme-challenge/ { root /var/www/certbot; }
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl http2;
  server_name DOMAIN_PLACEHOLDER WWW_DOMAIN_PLACEHOLDER;

  ssl_certificate     /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;

  include /etc/nginx/snippets/security_headers.conf;

  client_max_body_size 20m;
  proxy_read_timeout 120s;
  proxy_send_timeout 120s;

  resolver 127.0.0.11 ipv6=off;
  set $upstream CONTAINER_PLACEHOLDER:PORT_PLACEHOLDER;

  location /healthz { return 200 "ok"; add_header Content-Type text/plain; }

  location / {
    proxy_pass http://$upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
NGINX

# Replace placeholders
if [[ $INCLUDE_WWW -eq 1 ]]; then WWW_VAL="www.${DOMAIN}"; else WWW_VAL=""; fi
# Replace WWW placeholder FIRST to avoid partial replacement (DOMAIN inside WWW_DOMAIN)
sed -i "s/WWW_DOMAIN_PLACEHOLDER/${WWW_VAL}/g; s/DOMAIN_PLACEHOLDER/${DOMAIN}/g; s/CONTAINER_PLACEHOLDER/${CONTAINER}/g; s/PORT_PLACEHOLDER/${PORT}/g" "$VHOST_SRC"

sudo cp "$VHOST_SRC" "$VHOST_DST"
docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload || true

echo "[Done] https://${DOMAIN}/ is now proxied to ${CONTAINER}:${PORT}"

