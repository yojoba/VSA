# SSL Certificate System Analysis - Feb 2, 2026

## Executive Summary

**flowbiz.ai certificate issue: RESOLVED ✅**

The SSL certificate for flowbiz.ai was **actually renewed successfully** today (Feb 2, 2026) by the automated system, but NGINX wasn't serving the new certificate until we manually reloaded it.

**Root cause:** The renewal script detected renewals but didn't properly trigger an NGINX reload because the detection logic was checking for "Successfully renewed" text, which isn't present in newer Certbot output formats. It now checks for "The following renewals succeeded" as well.

---

## What I Found

### 1. The Renewal System Architecture

**Location:** `infra/scripts/check_renew_certs.sh`

**Trigger:** Daily cron job at 03:00 AM
```bash
0 3 * * * /home/fgrosal/dev/github/VSA/infra/scripts/check_renew_certs.sh >> /var/log/cert-renewal-cron.log 2>&1
```

**How it works:**
1. Checks if reverse-proxy NGINX container is running
2. Lists all Let's Encrypt certificates and their expiry dates
3. Runs `certbot renew --no-random-sleep-on-renew` inside certbot container
4. If renewals succeeded: Reloads NGINX to serve new certificates
5. Logs everything to `/var/log/cert-renewal.log` (or `/tmp/cert-renewal.log` if no write access)
6. Optionally sends Slack alerts (if `SLACK_WEBHOOK_URL` env var is set)

**Installation:**
```bash
make install-cert-monitoring  # Installs the cron job
make check-certs              # Manually run cert check/renewal
```

### 2. Today's Renewal Results (Feb 2, 2026)

From `/tmp/cert-renewal.log`:

**✅ Successfully renewed (7 certificates):**
- `dify.flowbiz.ai`
- **`flowbiz.ai` (and www.flowbiz.ai)** ← Your issue!
- `grafana.flowbiz.ai`
- `n8n.flowbiz.ai`
- `naturalpes-pharma.flowbiz.ai`
- `portfoliomanager.flowbiz.ai`
- `raphaelpittier.com` (and www)

**⏭️ Skipped (not yet due for renewal, >30 days remaining):**
- `cfo.flowbiz.ai` (expires Mar 4, 2026)
- `cfoweb.flowbiz.ai` (expires Mar 4, 2026)
- `don-camillo.flowbiz.ai` (expires Mar 17, 2026)
- `electroziles.ch` (expires Mar 8, 2026)
- `electroziles.flowbiz.ai` (expires Mar 5, 2026)
- `naturalpes-pharma.ch` (expires Apr 20, 2026)
- `promoflash.flowbiz.ai` (expires May 2, 2026)

**❌ Failed renewals (2 certificates):**
- `handsome.flowbiz.ai` - Error: `No such authorization` (stale ACME authorization issue)
- `roquimmobilier.ch` (and www) - Error: `unauthorized` - ACME CA received a 404 from https://apimo.net/ instead of the challenge file

### 3. Why flowbiz.ai Appeared Invalid

**Timeline:**

1. **Old certificate (issued Nov 3, 2025):**
   - Valid until: Feb 1, 2026 (EXPIRED YESTERDAY!)
   - This is what NGINX was serving when you checked the browser

2. **New certificate (issued Feb 2, 2026 at 09:02 AM):**
   - Valid until: May 3, 2026 (3 months)
   - Certbot successfully renewed it this morning
   - File existed on disk: `/srv/flowbiz/reverse-proxy/letsencrypt/live/flowbiz.ai/fullchain.pem`

3. **NGINX was not reloaded automatically** because:
   - The renewal script's detection logic was looking for "Successfully renewed" text
   - Newer Certbot versions output "The following renewals succeeded:" instead
   - Script didn't detect the renewal → didn't reload NGINX → old cert still served

**What I did to fix it:**
- Manually reloaded NGINX: `docker compose -f stacks/reverse-proxy/compose.yml exec nginx nginx -s reload`
- **After reload:** flowbiz.ai immediately started serving the new certificate (valid until May 3, 2026)

### 4. Certificate Verification

**Before fix (NGINX serving old cert):**
```
notBefore=Nov  3 11:10:54 2025 GMT
notAfter=Feb  1 11:10:53 2026 GMT   ← EXPIRED!
issuer=C = US, O = Let's Encrypt, CN = E8
subject=CN = flowbiz.ai
```

**On disk (new cert existed but not served):**
```
notBefore=Feb  2 09:02:12 2026 GMT
notAfter=May  3 09:02:11 2026 GMT   ← Valid for 3 months
issuer=C = US, O = Let's Encrypt, CN = E7
subject=CN = flowbiz.ai
```

**After fix (NGINX reloaded, now serving new cert):**
```
notBefore=Feb  2 09:02:12 2026 GMT
notAfter=May  3 09:02:11 2026 GMT   ← ✅ VALID!
issuer=C = US, O = Let's Encrypt, CN = E7
subject=CN = flowbiz.ai
```

---

## The Script Fix (Already Applied)

The renewal detection logic in `check_renew_certs.sh` has been updated to handle both old and new Certbot output formats:

**Old logic:**
```bash
if echo "$certbot_output" | grep -qi "Successfully renewed"; then
```

**New logic:**
```bash
if echo "$certbot_output" | grep -qiE "Successfully renewed|The following renewals succeeded"; then
```

This ensures NGINX reloads automatically after any successful renewal, regardless of Certbot version.

---

## Remaining Issues to Address

### 1. handsome.flowbiz.ai (Failed: No such authorization)

**Error:**
```
Failed to renew certificate handsome.flowbiz.ai with error: 
urn:ietf:params:acme:error:malformed :: The request message was malformed :: No such authorization
```

**Cause:** Stale ACME authorization in Certbot's state. This happens when:
- Domain DNS changed
- Previous renewal attempt was interrupted
- Certbot's internal state got corrupted

**Fix:**
```bash
# Force a fresh certificate issuance (not renewal)
docker compose -f stacks/reverse-proxy/compose.yml run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
  -d handsome.flowbiz.ai \
  --email ops@flowbiz.ai --agree-tos --no-eff-email --force-renewal

# Reload NGINX
docker compose -f stacks/reverse-proxy/compose.yml exec nginx nginx -s reload
```

### 2. roquimmobilier.ch (Failed: 404 from apimo.net)

**Error:**
```
Domain: roquimmobilier.ch
Type:   unauthorized
Detail: 212.83.187.122: Invalid response from https://apimo.net/fr/404-not-found: 404
```

**Cause:** DNS for `roquimmobilier.ch` is pointing to `212.83.187.122` (apimo.net), not your VPS.

**Diagnosis questions:**
- Is this domain supposed to be hosted on your VPS?
- Or is it a CNAME/redirect to apimo.net?
- If it should be on your VPS: Update DNS A/AAAA records to point to your VPS IP
- If it's intentionally at apimo.net: Remove the NGINX vhost and Let's Encrypt renewal config

**Fix (if DNS should point to your VPS):**
1. Update DNS records for `roquimmobilier.ch` and `www.roquimmobilier.ch` to your VPS IP
2. Wait for DNS propagation (check with `dig roquimmobilier.ch`)
3. Re-issue certificate:
   ```bash
   docker compose -f stacks/reverse-proxy/compose.yml run --rm --entrypoint certbot certbot \
     certonly --webroot -w /var/www/certbot \
     -d roquimmobilier.ch -d www.roquimmobilier.ch \
     --email ops@flowbiz.ai --agree-tos --no-eff-email --force-renewal
   ```

**Fix (if domain is intentionally elsewhere):**
```bash
# Remove vhost and renewal config
make unprovision-container domain=roquimmobilier.ch
make sync-vhosts
```

---

## Monitoring & Maintenance

### Check certificate expiry dates
```bash
make check-certs
```

### View renewal logs
```bash
tail -f /tmp/cert-renewal.log
# or (if you have write access to /var/log)
tail -f /var/log/cert-renewal.log
```

### View cron job logs
```bash
tail -f /var/log/cert-renewal-cron.log
```

### Force renewal of a specific certificate
```bash
docker compose -f stacks/reverse-proxy/compose.yml run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
  -d example.com -d www.example.com \
  --email ops@flowbiz.ai --agree-tos --no-eff-email --force-renewal

# Then reload NGINX
docker compose -f stacks/reverse-proxy/compose.yml exec nginx nginx -s reload
```

### Check if cron job is installed
```bash
crontab -l | grep check_renew_certs
```

### Re-install cron job if missing
```bash
make install-cert-monitoring
```

---

## NGINX Configuration Notes

Several vhosts are using the **deprecated** `listen ... http2` directive:
- `don-camillo.flowbiz.ai`
- `naturalpes-pharma.ch`
- `naturalpes-pharma.flowbiz.ai`
- `portfoliomanager.flowbiz.ai`
- `promoflash.flowbiz.ai`
- `raphaelpittier.com`

**Modern syntax:**
```nginx
server {
  listen 443 ssl http2;  # Old syntax (works but deprecated)
  # Should be:
  listen 443 ssl;
  http2 on;
}
```

**Impact:** None for now, but future NGINX versions may remove support. Consider updating these vhosts when convenient.

---

## Recommendations

1. **✅ flowbiz.ai is fixed** - now serving valid certificate until May 3, 2026
2. **Fix handsome.flowbiz.ai** - run the force-renewal command above
3. **Investigate roquimmobilier.ch** - check DNS and decide if it should be hosted on your VPS
4. **Log file location** - Current logs are in `/tmp/cert-renewal.log` (not persistent across reboots). Consider:
   - Creating `/var/log/cert-renewal.log` with proper permissions, or
   - Updating the script to use a persistent location in `/srv/flowbiz/`
5. **Slack notifications** - Set `SLACK_WEBHOOK_URL` env var if you want alerts on renewals/failures

---

## Quick Reference Commands

```bash
# Check all certificates and renew if needed
make check-certs

# Install automated daily monitoring (3 AM)
make install-cert-monitoring

# Reload NGINX after manual cert changes
docker compose -f stacks/reverse-proxy/compose.yml exec nginx nginx -s reload

# View certificate details for a domain
echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null | openssl x509 -noout -dates -issuer -subject

# Force renewal of a specific certificate
docker compose -f stacks/reverse-proxy/compose.yml run --rm --entrypoint certbot certbot \
  certonly --webroot -w /var/www/certbot \
  -d example.com \
  --email ops@flowbiz.ai --agree-tos --no-eff-email --force-renewal
```

---

**Analysis completed:** Feb 2, 2026 10:01 AM  
**Status:** flowbiz.ai certificate issue resolved ✅  
**Next actions:** Fix handsome.flowbiz.ai and investigate roquimmobilier.ch DNS
