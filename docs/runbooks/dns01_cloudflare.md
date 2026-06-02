# Cloudflare DNS-01 Cert Auto-Renewal Runbook

How to use the `compose.dns-cloudflare.yml` override on a VPS so its
Let's Encrypt certs are issued and renewed via the Cloudflare DNS-01
challenge instead of the default HTTP-01 webroot challenge.

## When to use

- The VPS is a warm standby — its public DNS does not resolve to its IP,
  so HTTP-01 challenges from this host would fail.
- You want to issue a wildcard cert (HTTP-01 doesn't support wildcards).
- The domains live in a Cloudflare zone you can scope an API token to.

For the live host whose DNS A record points at it, prefer plain HTTP-01
webroot — it needs no extra credentials and is the default `compose.yml`
configuration.

## Prerequisites

- Cloudflare API token with `Zone:DNS:Edit` permission scoped to the zone(s)
  whose certs will be issued from this VPS.
- The reverse-proxy stack already deployed (or about to be) on the VPS.
- The VPS can reach `https://api.cloudflare.com` outbound.

## Setup

On the VPS:

```bash
# 1. Drop the API token in a credentials file (mode 0600, root-owned)
sudo mkdir -p /srv/flowbiz/reverse-proxy/cloudflare
sudo tee /srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini >/dev/null <<EOF
dns_cloudflare_api_token = cfut_REPLACE_WITH_REAL_TOKEN
EOF
sudo chmod 600 /srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini

# 2. Tell docker compose to also load the dns-cloudflare override file
sudo tee /home/$USER/dev/github/VSA/stacks/reverse-proxy/.env >/dev/null <<'EOF'
COMPOSE_FILE=compose.yml:compose.dns-cloudflare.yml
EOF

# 3. Recreate the certbot container with the new image + mount
cd ~/dev/github/VSA/stacks/reverse-proxy
docker compose up -d certbot
```

## Issuing a cert

```bash
docker exec reverse-proxy-certbot certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /cf/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 60 \   # 30 flakes for apex+www (2 TXT records)
  --email alexandre@netcool.ch --agree-tos --no-eff-email \
  -d <domain>            # add `-d www.<domain>` for the apex if needed
```

The cert lands in `/srv/flowbiz/reverse-proxy/letsencrypt/live/<domain>/`.
The matching `renewal/<domain>.conf` records `authenticator = dns-cloudflare`,
so the existing 12-hourly `certbot renew` loop in the container handles
future renewals without further configuration.

## Verifying

```bash
docker exec reverse-proxy-certbot certbot renew --dry-run
```

Expected output ends with `Congratulations, all simulated renewals succeeded`.
The dashboard `/api/certs` endpoint should show the new cert under the
correct `vps_id` within ~30s (next agent tick).

## Token rotation

Edit `/srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini` in place — no
container restart needed. Certbot reads the file at each invocation.

## Adding a domain to an existing setup

If the new domain is in a zone the existing token already covers, just
run the `certonly` command above. If it's a new zone, either issue a
new token with broader scope or replace the credentials file.

## Active deployments

| VPS | Token zone | Domains issued via DNS-01 |
| --- | --- | --- |
| vps-02 | `lokalflash.ch` | `lokalflash.ch` (+`www`), `app.lokalflash.ch` |
| vps-03 | `lokalflash.ch` | `lokalflash.ch` (+`www`), `app.lokalflash.ch` |

> **vps-02 added 2026-06-01.** It was originally on HTTP-01 webroot, but its
> domains are Cloudflare-proxied and its renewal confs pointed at a dead ACME
> **v1** account, so `certbot renew` silently failed for months. Migrating it to
> DNS-01 (same setup as vps-03) fixed both problems at once.

> **Note:** as of `fd0455f`, `vsa stack up reverse-proxy` honors this `.env`
> `COMPOSE_FILE` override (the `docker.compose_*` helpers expand it into `-f`
> flags), so it correctly keeps certbot on the `dns-cloudflare` image. `docker
> compose up -d certbot` from `~/dev/github/VSA/stacks/reverse-proxy/` still
> works as a manual equivalent. **After pulling a CLI change, reinstall with
> `uv tool install . --reinstall --no-cache`** — plain `--force` serves a cached
> `0.1.0` wheel and your change won't land.
>
> If you hit *"Another instance of Certbot is already running"*, an orphaned
> `--dry-run` exec is holding the lock: `docker exec reverse-proxy-certbot sh -c
> 'pkill -9 -f dry-run; rm -f /etc/letsencrypt/.certbot.lock /var/log/letsencrypt/.certbot.lock'`.
