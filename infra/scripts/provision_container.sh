#!/usr/bin/env bash
set -Eeuo pipefail

# provision_container.sh
# Auto-provision a running container behind reverse proxy by detecting container from published port
# Usage:
#   ./infra/scripts/provision_container.sh --domain <domain> --external-port <port> [--internal-port <port>] [--no-www]

usage() {
  cat <<USAGE >&2
Usage: $0 --domain <domain> --external-port <port> [--internal-port <port>] [--no-www]

  --domain         Domain name (e.g., example.com)
  --external-port  Published host port (e.g., 3005)
  --internal-port  Container internal port (default: 3000)
  --no-www         Skip www subdomain

Example:
  # Auto-detect container exposing host port 3005, proxy to internal 3000
  $0 --domain example.com --external-port 3005

  # Explicit internal port
  $0 --domain api.example.com --external-port 8080 --internal-port 8080 --no-www
USAGE
  exit 1
}

DOMAIN=""
EXTERNAL_PORT=""
INTERNAL_PORT="3000"
INCLUDE_WWW=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    --external-port) EXTERNAL_PORT="$2"; shift 2;;
    --internal-port) INTERNAL_PORT="$2"; shift 2;;
    --no-www) INCLUDE_WWW=0; shift;;
    *) usage;;
  esac
done

if [[ -z "$DOMAIN" || -z "$EXTERNAL_PORT" ]]; then
  usage
fi

echo "[0/7] Detect container listening on host port $EXTERNAL_PORT"
CONTAINER=$(docker ps --format '{{.Names}}\t{{.Ports}}' | grep -E "0\.0\.0\.0:${EXTERNAL_PORT}->" | awk '{print $1}' | head -n 1)

if [[ -z "$CONTAINER" ]]; then
  echo "Error: No container found publishing port $EXTERNAL_PORT" >&2
  exit 1
fi

echo "  → Found container: $CONTAINER"

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
VHOST_SRC="$STACK_DIR/nginx/conf.d/${DOMAIN}.conf"
VHOST_DST="/srv/flowbiz/reverse-proxy/nginx/conf.d/${DOMAIN}.conf"

echo "[1/7] Ensure reverse-proxy mounts exist"
docker run --rm -v /srv/flowbiz/reverse-proxy:/target alpine sh -c 'mkdir -p /target/{letsencrypt,certbot-www,logs,nginx/conf.d,nginx/snippets}' || true

echo "[1a/7] Ensure certbot webroot permissions"
docker run --rm -v /srv/flowbiz/reverse-proxy/certbot-www:/var/www/certbot alpine sh -c '\
  chmod 755 /var/www/certbot && \
  mkdir -p /var/www/certbot/.well-known/acme-challenge && \
  chmod -R 755 /var/www/certbot/.well-known \
' || true

echo "[2/7] Attach container '$CONTAINER' to flowbiz_ext (idempotent)"
if ! docker network inspect flowbiz_ext >/dev/null 2>&1; then
  docker network create flowbiz_ext >/dev/null
fi
if ! docker inspect -f '{{json .NetworkSettings.Networks}}' "$CONTAINER" 2>/dev/null | grep -q 'flowbiz_ext'; then
  docker network connect flowbiz_ext "$CONTAINER" || true
fi

echo "[3/7] Generate temporary HTTP-only vhost for ACME at $VHOST_SRC"
cat >"$VHOST_SRC" <<'NGINX'
server {
  listen 80;
  server_name DOMAIN_PLACEHOLDER WWW_DOMAIN_PLACEHOLDER;
  location /.well-known/acme-challenge/ { root /var/www/certbot; }
  location / { return 301 https://$host$request_uri; }
}
NGINX

# Replace placeholders
if [[ $INCLUDE_WWW -eq 1 ]]; then
  sed -i "s/WWW_DOMAIN_PLACEHOLDER/www.${DOMAIN}/g; s/DOMAIN_PLACEHOLDER/${DOMAIN}/g" "$VHOST_SRC"
else
  sed -i "s/WWW_DOMAIN_PLACEHOLDER//g; s/DOMAIN_PLACEHOLDER/${DOMAIN}/g" "$VHOST_SRC"
fi

echo "[4/7] Deploy HTTP-only vhost and reload NGINX"
docker run --rm -v "$REPO_DIR/stacks/reverse-proxy/nginx/conf.d:/src" -v /srv/flowbiz/reverse-proxy/nginx/conf.d:/dst alpine sh -c "cp /src/${DOMAIN}.conf /dst/${DOMAIN}.conf"
docker compose -f "$STACK_DIR/compose.yml" up -d --build
docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload || true

echo "[5/7] Issue Let's Encrypt certificate for ${DOMAIN}${INCLUDE_WWW:+ and www.${DOMAIN}}"
CERT_DOMAINS=(-d "$DOMAIN")
if [[ $INCLUDE_WWW -eq 1 ]]; then CERT_DOMAINS+=(-d "www.$DOMAIN"); fi
docker compose -f "$STACK_DIR/compose.yml" run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot "${CERT_DOMAINS[@]}" \
  --email ops@flowbiz.ai --agree-tos --no-eff-email \
  --keep-until-expiring --non-interactive

echo "[6/7] Generate final HTTPS vhost with upstream and reload"
cat >"$VHOST_SRC" <<'NGINX'
server {
  listen 80;
  server_name DOMAIN_PLACEHOLDER WWW_DOMAIN_PLACEHOLDER;
  location /.well-known/acme-challenge/ { root /var/www/certbot; }
  location / { return 301 https://$host$request_uri; }
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
sed -i "s/WWW_DOMAIN_PLACEHOLDER/${WWW_VAL}/g; s/DOMAIN_PLACEHOLDER/${DOMAIN}/g; s/CONTAINER_PLACEHOLDER/${CONTAINER}/g; s/PORT_PLACEHOLDER/${INTERNAL_PORT}/g" "$VHOST_SRC"

docker run --rm -v "$REPO_DIR/stacks/reverse-proxy/nginx/conf.d:/src" -v /srv/flowbiz/reverse-proxy/nginx/conf.d:/dst alpine sh -c "cp /src/${DOMAIN}.conf /dst/${DOMAIN}.conf"
docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload || true

echo "[7/7] Verify HTTPS"
sleep 2
curl -sSk -D - -o /dev/null --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/" | head -n 10 || true

echo ""
echo "[Done] https://${DOMAIN}/ is now proxied to ${CONTAINER}:${INTERNAL_PORT}"
echo "  Container: $CONTAINER (detected from host port $EXTERNAL_PORT)"



