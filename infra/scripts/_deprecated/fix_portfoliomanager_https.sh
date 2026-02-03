#!/usr/bin/env bash
set -Eeuo pipefail

# fix_portfoliomanager_https.sh
# Fix HTTPS for portfoliomanager.flowbiz.ai

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
STACK_DIR="$REPO_DIR/stacks/reverse-proxy"
VHOST_FILE="$STACK_DIR/nginx/conf.d/portfoliomanager.flowbiz.ai.conf"
MOUNT_DIR="/srv/flowbiz/reverse-proxy/nginx/conf.d"

echo "========================================="
echo "Fixing HTTPS for portfoliomanager.flowbiz.ai"
echo "========================================="

# Step 1: Check if reverse proxy is running
echo "[1/6] Checking reverse proxy status..."
if ! docker compose -f "$STACK_DIR/compose.yml" ps --services --filter "status=running" | grep -q nginx; then
  echo "⚠️  Reverse proxy NGINX is not running. Starting it..."
  docker compose -f "$STACK_DIR/compose.yml" up -d nginx
  sleep 5
else
  echo "✅ Reverse proxy is running"
fi

# Step 2: Sync vhost files
echo "[2/6] Syncing vhost files from repo to mounted directory..."
sudo rsync -av --delete "$STACK_DIR/nginx/conf.d/" "$MOUNT_DIR/"
echo "✅ Vhost files synced"

# Step 3: Check certificate status
echo "[3/6] Checking certificate status..."
CERT_PATH="/srv/flowbiz/reverse-proxy/letsencrypt/live/portfoliomanager.flowbiz.ai/fullchain.pem"

if [ -f "$CERT_PATH" ]; then
  echo "✅ Certificate file exists"
  EXPIRY=$(docker compose -f "$STACK_DIR/compose.yml" exec -T nginx sh -c "openssl x509 -noout -enddate -in /etc/letsencrypt/live/portfoliomanager.flowbiz.ai/fullchain.pem 2>/dev/null | cut -d= -f2" || echo "")
  if [ -n "$EXPIRY" ]; then
    echo "   Certificate expires: $EXPIRY"
    # Check if expired or expiring soon (within 30 days)
    EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null || echo "0")
    NOW_EPOCH=$(date +%s)
    DAYS_UNTIL_EXPIRY=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    if [ $DAYS_UNTIL_EXPIRY -lt 30 ]; then
      echo "⚠️  Certificate expires in $DAYS_UNTIL_EXPIRY days, renewing..."
      RENEW_NEEDED=1
    else
      echo "✅ Certificate is valid for $DAYS_UNTIL_EXPIRY more days"
      RENEW_NEEDED=0
    fi
  else
    echo "⚠️  Could not read certificate expiry"
    RENEW_NEEDED=1
  fi
else
  echo "❌ Certificate file not found"
  RENEW_NEEDED=1
fi

# Step 4: Issue/renew certificate if needed
if [ "${RENEW_NEEDED:-1}" = "1" ]; then
  echo "[4/6] Issuing/renewing certificate..."
  docker compose -f "$STACK_DIR/compose.yml" run --rm --entrypoint certbot certbot \
    certonly --webroot -w /var/www/certbot \
    -d portfoliomanager.flowbiz.ai \
    --email ops@flowbiz.ai --agree-tos --no-eff-email \
    --force-renewal 2>&1 | grep -v "Skipping virtualenv" || true
  echo "✅ Certificate issued/renewed"
else
  echo "[4/6] Certificate is valid, skipping renewal"
fi

# Step 5: Test NGINX config and reload
echo "[5/6] Testing NGINX configuration..."
if docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -t 2>&1 | grep -q "successful"; then
  echo "✅ NGINX configuration is valid"
  echo "   Reloading NGINX..."
  docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -s reload
  echo "✅ NGINX reloaded"
else
  echo "❌ NGINX configuration test failed"
  docker compose -f "$STACK_DIR/compose.yml" exec nginx nginx -t
  exit 1
fi

# Step 6: Check portfolio_backend container
echo "[6/6] Checking portfolio_backend container..."
PORTFOLIO_CONTAINER=$(docker ps --format '{{.Names}}' | grep -i portfolio | head -n 1 || echo "")
if [ -n "$PORTFOLIO_CONTAINER" ]; then
  echo "✅ Found portfolio container: $PORTFOLIO_CONTAINER"
  # Check if on flowbiz_ext network
  if docker inspect "$PORTFOLIO_CONTAINER" 2>/dev/null | grep -q "flowbiz_ext"; then
    echo "✅ Container is on flowbiz_ext network"
  else
    echo "⚠️  Container is NOT on flowbiz_ext network. Attaching..."
    docker network connect flowbiz_ext "$PORTFOLIO_CONTAINER" 2>/dev/null || echo "   (May already be connected or network doesn't exist)"
  fi
else
  echo "⚠️  No portfolio container found. Make sure portfolio_backend is running."
fi

echo ""
echo "========================================="
echo "✅ Fix complete!"
echo "========================================="
echo ""
echo "Test HTTPS connection:"
echo "  curl -I https://portfoliomanager.flowbiz.ai/healthz"
echo ""







