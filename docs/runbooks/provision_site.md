# How-to: Provision a Website Behind the Reverse Proxy

This guide shows how to expose an existing container at a domain with HTTPS using the `vsa` CLI.

## Prerequisites
- Container is running and exposes an internal port (e.g., 3000).
- DNS A/AAAA records point the domain to the VPS public IP.
- Reverse proxy stack is up: `vsa stack up reverse-proxy`.
- `vsa` CLI installed: `cd apps/vps-admin-cli && uv tool install .`

## Quick method (recommended)

Auto-detect container from published host port:
```bash
vsa site provision --domain example.com --port 3000 --detect --external-port 3005
```

Manual container name:
```bash
vsa site provision --domain example.com --container my-app-1 --port 3000
```

What happens:
1. Connects the container to `flowbiz_ext` network (idempotent)
2. Generates HTTP-only vhost for ACME challenge
3. Deploys and reloads NGINX
4. Issues Let's Encrypt certificate (including www)
5. Generates final HTTPS vhost with upstream proxy
6. Validates config with `nginx -t`, then reloads
7. Logs the operation to `/var/log/vsa/audit.jsonl`

### Options

```bash
# Skip www subdomain
vsa site provision --domain api.example.com --container api-1 --port 8080 --no-www

# Skip certificate issuance (HTTP-only)
vsa site provision --domain staging.example.com --container staging-1 --port 3000 --skip-cert
```

### Make target (delegates to CLI)

```bash
make provision-container domain=example.com port=3005
make provision-container domain=api.example.com port=8080 nowww=true
```

## Unprovision

```bash
vsa site unprovision --domain example.com
vsa vhost sync   # Apply changes
```

## List all sites

```bash
vsa site list
```

## Verify

```bash
curl -sS -D - -o /dev/null https://example.com/
vsa cert status
```

## Legacy method

The bash scripts in `infra/scripts/` still work but are deprecated:
```bash
./infra/scripts/provision_container.sh --domain example.com --external-port 3005
./infra/scripts/provision_site.sh --domain example.com --container my-app --port 3000
```
