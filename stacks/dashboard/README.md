# Dashboard Stack

Centralized VSA management dashboard: FastAPI backend + Next.js frontend + PostgreSQL 16.

## Deploy

```bash
mkdir -p /srv/flowbiz/dashboard/data/{postgres}
cp .env.example /srv/flowbiz/dashboard/env/.env
# Edit .env with real credentials

docker compose up -d --build
make provision-container domain=dashboard.flowbiz.ai port=3000
```

## Architecture

- `dashboard-db` — PostgreSQL 16 (internal network only)
- `dashboard-api` — FastAPI on port 8000 (proxied via NGINX at `/api/*`)
- `dashboard-ui` — Next.js on port 3000 (proxied via NGINX at `/*`)

## NGINX Routing

The dashboard vhost at `stacks/reverse-proxy/nginx/conf.d/dashboard.flowbiz.ai.conf` routes:
- `/api/*` to `dashboard-api:8000`
- `/*` to `dashboard-ui:3000`
