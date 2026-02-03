#!/usr/bin/env bash
set -Eeuo pipefail

# unprovision_site.sh
# Removes a site's NGINX vhost and optional auth file from the repo.
# NOTE: This does NOT stop or remove the Docker container itself, it only
# unhooks it from the reverse proxy. Run sync_vhosts.sh afterwards to apply.

usage() {
  echo "Usage: $0 --domain <domain>" >&2
  exit 1
}

DOMAIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$DOMAIN" ]]; then
  echo "[unprovision-site] ERROR: --domain is required" >&2
  usage
fi

REPO_CONF_DIR="/home/fgrosal/dev/github/VSA/stacks/reverse-proxy/nginx/conf.d"
REPO_AUTH_DIR="/home/fgrosal/dev/github/VSA/stacks/reverse-proxy/nginx/auth"

CONF_FILE="${REPO_CONF_DIR}/${DOMAIN}.conf"
AUTH_FILE="${REPO_AUTH_DIR}/${DOMAIN}.htpasswd"

echo "[unprovision-site] Domain: $DOMAIN"

if [[ -f "$CONF_FILE" ]]; then
  echo "[unprovision-site] Removing vhost file: $CONF_FILE"
  rm -f "$CONF_FILE"
else
  echo "[unprovision-site] No vhost file found at: $CONF_FILE (nothing to do)"
fi

if [[ -f "$AUTH_FILE" ]]; then
  echo "[unprovision-site] Removing auth file: $AUTH_FILE"
  rm -f "$AUTH_FILE"
else
  echo "[unprovision-site] No auth file found at: $AUTH_FILE (nothing to do)"
fi

echo "[unprovision-site] Done. Now run: sudo ./infra/scripts/sync_vhosts.sh"






