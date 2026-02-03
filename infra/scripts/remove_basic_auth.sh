#!/usr/bin/env bash
set -Eeuo pipefail

# remove_basic_auth.sh
# Removes HTTP Basic Auth directives from a domain's NGINX vhost in the repo.
# This operates on the repo copy only; run sync_vhosts.sh afterwards to apply.

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
  echo "[remove-basic-auth] ERROR: --domain is required" >&2
  usage
fi

REPO_VHOST_DIR="/home/fgrosal/dev/github/VSA/stacks/reverse-proxy/nginx/conf.d"
VHOST_FILE="${REPO_VHOST_DIR}/${DOMAIN}.conf"

if [[ ! -f "$VHOST_FILE" ]]; then
  echo "[remove-basic-auth] ERROR: vhost file not found: $VHOST_FILE" >&2
  exit 1
fi

TMP_FILE="$(mktemp)"

echo "[remove-basic-auth] Updating vhost file: $VHOST_FILE"

# Strip lines containing auth_basic or auth_basic_user_file
awk '!/auth_basic / && !/auth_basic_user_file /' "$VHOST_FILE" > "$TMP_FILE"

mv "$TMP_FILE" "$VHOST_FILE"

echo "[remove-basic-auth] Done. Now run: sudo ./infra/scripts/sync_vhosts.sh"






