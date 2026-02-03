#!/usr/bin/env bash
set -Eeuo pipefail

# check_renew_certs.sh
# Daily SSL certificate monitoring and auto-renewal

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
LOG_FILE="/var/log/cert-renewal.log"
SLACK_WEBHOOK="${SLACK_WEBHOOK_URL:-}"

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

send_slack() {
  local message="$1"
  if [[ -n "$SLACK_WEBHOOK" ]]; then
    curl -sS -X POST "$SLACK_WEBHOOK" \
      -H 'Content-Type: application/json' \
      -d "{\"text\":\"$message\"}" >/dev/null 2>&1 || true
  fi
}

main() {
  log "========================================="
  log "SSL Certificate Check & Renewal"
  log "========================================="
  
  # Check if reverse proxy is running
  if ! docker compose -f "$STACK_DIR/compose.yml" ps --services --filter "status=running" | grep -q nginx; then
    log "ERROR: Reverse proxy NGINX is not running"
    send_slack "🚨 SSL Check Failed: NGINX container is not running"
    exit 1
  fi
  
  # List all certificates
  log "Certificates on this server:"
  docker compose -f "$STACK_DIR/compose.yml" exec -T nginx sh -c '
    for dir in /etc/letsencrypt/live/*/; do
      [ -d "$dir" ] || continue
      domain=$(basename "$dir")
      [ "$domain" = "README" ] && continue
      cert="$dir/cert.pem"
      if [ -f "$cert" ]; then
        expiry=$(openssl x509 -noout -enddate -in "$cert" 2>/dev/null | cut -d= -f2)
        echo "  ✓ $domain | Expires: $expiry"
      fi
    done
  ' 2>/dev/null | tee -a "$LOG_FILE"
  
  log ""
  log "Running certbot renew (checks all certs, renews if <30 days to expiry)..."
  
  # Capture certbot output
  local certbot_output
  certbot_output=$(docker compose -f "$STACK_DIR/compose.yml" run --rm --entrypoint certbot certbot \
    renew --no-random-sleep-on-renew 2>&1)
  
  # Log full output
  echo "$certbot_output" >> "$LOG_FILE"
  
  # Always reload NGINX after certbot renew to ensure new certs are served
  # (Certbot only updates files if renewal actually happened, so this is safe)
  log ""
  log "Reloading NGINX to pick up any certificate changes..."
  docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload 2>&1 | tee -a "$LOG_FILE"
  log ""
  
  # Parse output to determine what happened and send appropriate notifications
  if echo "$certbot_output" | grep -qiE "Successfully renewed|The following renewals succeeded"; then
    # Extract a concise summary of renewed certificates
    local renewed
    renewed=$(echo "$certbot_output" | grep -E "Successfully renewed|/etc/letsencrypt/live/.*/fullchain.pem \(success\)" || true)
    log "✅ CERTIFICATES RENEWED:"
    echo "$renewed" | tee -a "$LOG_FILE"
    
    send_slack "✅ SSL Certificates Renewed\n$renewed"
  
  elif echo "$certbot_output" | grep -qi "not yet due for renewal"; then
    # No certificates needed renewal this run
    log "✅ All certificates valid (not due for renewal)"
  
  fi
  
  # Check for failures (can coexist with successful renewals)
  if echo "$certbot_output" | grep -qiE "Failed to renew|renewal(s) failed"; then
    local failures
    failures=$(echo "$certbot_output" | grep -B 2 -A 5 "Failed to renew" || echo "$certbot_output")
    log ""
    log "❌ SOME RENEWALS FAILED:"
    echo "$failures" | tee -a "$LOG_FILE"
    send_slack "🚨 SSL Renewal Failed\n$failures"
  fi
  
  log "========================================="
  log "Certificate check completed"
  log "========================================="
}

# Create log file if it doesn't exist
touch "$LOG_FILE" 2>/dev/null || LOG_FILE="/tmp/cert-renewal.log"

main "$@"
