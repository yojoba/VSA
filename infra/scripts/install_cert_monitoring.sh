#!/usr/bin/env bash
set -Eeuo pipefail

# install_cert_monitoring.sh
# Install daily SSL certificate monitoring as a cron job

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT_PATH="$REPO_DIR/infra/scripts/check_renew_certs.sh"
CRON_TIME="0 3 * * *"  # Daily at 3 AM

echo "[1/3] Ensure check_renew_certs.sh is executable"
chmod +x "$SCRIPT_PATH"

echo "[2/3] Create cron job for daily certificate checks"
CRON_LINE="$CRON_TIME $SCRIPT_PATH >> /var/log/cert-renewal-cron.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -qF "check_renew_certs.sh"; then
  echo "  → Cron job already exists, updating..."
  (crontab -l 2>/dev/null | grep -vF "check_renew_certs.sh"; echo "$CRON_LINE") | crontab -
else
  echo "  → Adding new cron job..."
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
fi

echo "[3/3] Verify cron job installed"
crontab -l | grep check_renew_certs.sh

echo ""
echo "✅ SSL certificate monitoring installed!"
echo "  - Runs daily at 3:00 AM"
echo "  - Checks all certificates"
echo "  - Auto-renews if expiring within 30 days"
echo "  - Logs to /var/log/cert-renewal.log and /var/log/cert-renewal-cron.log"
echo ""
echo "Manual run: $SCRIPT_PATH"
echo "View logs: tail -f /var/log/cert-renewal.log"

