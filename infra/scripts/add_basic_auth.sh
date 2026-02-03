#!/usr/bin/env bash
set -Eeuo pipefail

# add_basic_auth.sh
# Add HTTP Basic Auth protection to a domain
# Usage:
#   ./infra/scripts/add_basic_auth.sh --domain <domain> --user <username> --password <password>

usage() {
  cat <<USAGE >&2
Usage: $0 --domain <domain> --user <username> --password <password>

  --domain     Domain to protect (e.g., example.com)
  --user       Username for basic auth
  --password   Password for basic auth

Example:
  $0 --domain staging.example.com --user admin --password secret123
USAGE
  exit 1
}

DOMAIN=""
USER=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2;;
    --user) USER="$2"; shift 2;;
    --password) PASSWORD="$2"; shift 2;;
    *) usage;;
  esac
done

if [[ -z "$DOMAIN" || -z "$USER" || -z "$PASSWORD" ]]; then
  usage
fi

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
VHOST_SRC="$STACK_DIR/nginx/conf.d/${DOMAIN}.conf"
HTPASSWD_FILE="$STACK_DIR/nginx/auth/${DOMAIN}.htpasswd"

echo "[1/4] Check if vhost exists"
if [[ ! -f "$VHOST_SRC" ]]; then
  echo "Error: Vhost for $DOMAIN not found at $VHOST_SRC" >&2
  echo "Please provision the site first using provision_container.sh" >&2
  exit 1
fi

echo "[2/4] Generate .htpasswd file"
mkdir -p "$STACK_DIR/nginx/auth"

# Generate htpasswd entry using openssl
ENCRYPTED=$(openssl passwd -apr1 "$PASSWORD")
echo "$USER:$ENCRYPTED" > "$HTPASSWD_FILE"
echo "  → Created $HTPASSWD_FILE"

echo "[3/4] Update vhost to include basic auth"
# Check if auth already exists
if grep -q "auth_basic" "$VHOST_SRC"; then
  echo "  → Basic auth already configured in vhost"
else
  # Add auth_basic directives after ssl_certificate lines
  sed -i "/include \/etc\/nginx\/snippets\/security_headers.conf;/a\\
\\
  # HTTP Basic Auth\\
  auth_basic \"Restricted Access\";\\
  auth_basic_user_file /etc/nginx/auth/${DOMAIN}.htpasswd;" "$VHOST_SRC"
  echo "  → Added basic auth to vhost"
fi

echo "[4/4] Deploy and reload NGINX"
# Create auth directory in mounted volume
docker run --rm -v /srv/flowbiz/reverse-proxy/nginx:/target alpine sh -c 'mkdir -p /target/auth' || true

# Copy htpasswd file into mounted auth directory
docker run --rm \
  -v "$REPO_DIR/stacks/reverse-proxy/nginx/auth:/src" \
  -v /srv/flowbiz/reverse-proxy/nginx/auth:/dst \
  alpine sh -c "cp /src/${DOMAIN}.htpasswd /dst/${DOMAIN}.htpasswd"

# Normalize permissions so NGINX can read the htpasswd file
docker run --rm \
  -e DOMAIN="$DOMAIN" \
  -v /srv/flowbiz/reverse-proxy/nginx/auth:/auth \
  alpine sh -c '\
    chmod 755 /auth && \
    if [ -f "/auth/${DOMAIN}.htpasswd" ]; then \
      chown root:root "/auth/${DOMAIN}.htpasswd" || true; \
      chmod 644 "/auth/${DOMAIN}.htpasswd" || true; \
    fi \
  ' || true

# Copy updated vhost
docker run --rm \
  -v "$REPO_DIR/stacks/reverse-proxy/nginx/conf.d:/src" \
  -v /srv/flowbiz/reverse-proxy/nginx/conf.d:/dst \
  alpine sh -c "cp /src/${DOMAIN}.conf /dst/${DOMAIN}.conf"

# Update compose to mount auth directory
if ! grep -q "/srv/flowbiz/reverse-proxy/nginx/auth:/etc/nginx/auth" "$STACK_DIR/compose.yml"; then
  echo "  → Adding auth volume to compose.yml"
  sed -i '/- \/srv\/flowbiz\/reverse-proxy\/nginx\/snippets:\/etc\/nginx\/snippets:ro/a\      - /srv/flowbiz/reverse-proxy/nginx/auth:/etc/nginx/auth:ro' "$STACK_DIR/compose.yml"
fi

# Reload NGINX
docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload

echo ""
echo "[Done] Basic auth enabled for https://$DOMAIN/"
echo "  Username: $USER"
echo "  Password: (hidden)"
echo ""
echo "To remove: edit $VHOST_SRC and remove auth_basic lines, then reload NGINX"


