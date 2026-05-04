#!/usr/bin/env bash
# Bring up the observability-agent stack on a remote VPS.
#
# Idempotent — safe to re-run. Reads credentials from /srv/flowbiz/
# observability-agent/env/.env if present, otherwise interactively prompts.
#
# Run AS THE USER who will own the stack (not root). The VSA CLI must
# already be installed (uv tool install in the cli dir).

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STACK_DIR="$REPO_ROOT/stacks/observability-agent"
ENV_DIR="/srv/flowbiz/observability-agent/env"
ENV_FILE="$ENV_DIR/.env"

log() { printf '[observability-agent] %s\n' "$*"; }
err() { printf '[observability-agent] ERROR: %s\n' "$*" >&2; exit 1; }

# --- prerequisites ----------------------------------------------------
[ -d "$STACK_DIR" ]                       || err "stack dir not found: $STACK_DIR (is the repo up to date?)"
command -v vsa >/dev/null 2>&1            || err "vsa CLI not installed (run: cd apps/vps-admin-cli && uv tool install .)"
docker network inspect flowbiz_ext >/dev/null 2>&1 \
                                          || err "docker network 'flowbiz_ext' missing — run infra/scripts/bootstrap_vps.sh first"

# --- .env -------------------------------------------------------------
sudo mkdir -p "$ENV_DIR"
sudo chown "$(id -u):$(id -g)" "$ENV_DIR"

if [ ! -f "$ENV_FILE" ]; then
    log "creating $ENV_FILE from template"
    cp "$STACK_DIR/.env.example" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
fi

# Pull existing values so prompts can show current defaults.
# shellcheck disable=SC1090
. "$ENV_FILE"

prompt_if_unset() {
    local var="$1" current="${!1}" default="$2"
    if [ -z "$current" ] || [ "$current" = "replace-me" ] || [ "$current" = "vps-02" ] && [ "$var" = "VSA_VPS_ID" ]; then
        printf '%s [%s]: ' "$var" "${default:-$current}" >&2
        read -r value
        value="${value:-${default:-$current}}"
        # Replace the line in the .env file (creating it if missing).
        if grep -q "^${var}=" "$ENV_FILE"; then
            # GNU vs BSD sed compatibility — write to a tmp file then mv.
            sed "s|^${var}=.*|${var}=${value}|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
        else
            printf '%s=%s\n' "$var" "$value" >> "$ENV_FILE"
        fi
        export "$var"="$value"
    fi
}

# Detect VPS ID from /etc/vsa/agent.env if available.
default_vps_id="$(awk -F= '/^VSA_VPS_ID=/{print $2; exit}' /etc/vsa/agent.env 2>/dev/null || true)"
prompt_if_unset VSA_VPS_ID "$default_vps_id"
prompt_if_unset LOKI_URL  "https://loki.flowbiz.ai/loki/api/v1/push"
prompt_if_unset LOKI_BASIC_AUTH_USER "promtail"
prompt_if_unset LOKI_BASIC_AUTH_PASSWORD ""

# Stack-local symlink so docker-compose finds the .env without a custom path.
ln -sf "$ENV_FILE" "$STACK_DIR/.env"

# --- preflight: can we reach Loki? -----------------------------------
log "preflight: checking $LOKI_URL"
http_code=$(curl -sS -o /dev/null -w '%{http_code}' \
    -u "$LOKI_BASIC_AUTH_USER:$LOKI_BASIC_AUTH_PASSWORD" \
    --max-time 10 "$LOKI_URL" -X POST -H 'Content-Type: application/json' -d '{}' || echo "000")
case "$http_code" in
    400|204|200) log "preflight OK (HTTP $http_code — empty payload accepted/rejected, auth+network work)" ;;
    401)         err "401 Unauthorized — basic-auth credentials wrong" ;;
    403)         err "403 Forbidden — this VPS public IP not in nginx allow-list on the hub" ;;
    000)         err "network unreachable — DNS or firewall problem" ;;
    *)           log "preflight returned HTTP $http_code — proceeding anyway" ;;
esac

# --- bring it up ------------------------------------------------------
log "vsa stack up observability-agent"
vsa stack up observability-agent

log "tailing Promtail for 5 s to confirm no boot errors…"
sleep 5
docker logs --tail 30 observability-agent-promtail 2>&1 | sed 's/^/  /'

log "done. Verify on the hub: open Grafana and query  {vps_id=\"$VSA_VPS_ID\"} |= \"\""
