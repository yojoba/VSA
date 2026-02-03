# Dashboard Stack

Centralized VSA management dashboard: FastAPI backend + Next.js frontend + PostgreSQL 16.

**Live at:** `https://dashboard.flowbiz.ai/` (HTTP Basic Auth)

## Deploy

```bash
# Create data and env directories
mkdir -p /srv/flowbiz/dashboard/{data,env,logs}

# Create .env from template (or symlink)
cp .env.example /srv/flowbiz/dashboard/env/.env
# Edit .env with real POSTGRES_PASSWORD and VSA_API_TOKEN
ln -sf /srv/flowbiz/dashboard/env/.env .env

# Build and start
docker compose up -d --build

# Issue TLS cert and configure NGINX
# (vhost config already exists at stacks/reverse-proxy/nginx/conf.d/dashboard.flowbiz.ai.conf)
certbot certonly --webroot -w /var/www/certbot -d dashboard.flowbiz.ai
```

## Architecture

- `dashboard-db` — PostgreSQL 16 (internal network only, data at `/srv/flowbiz/dashboard/data/postgres`)
- `dashboard-api` — FastAPI on port 8000 (proxied via NGINX at `/api/*`)
- `dashboard-ui` — Next.js on port 3000 (proxied via NGINX at `/*`)

## Docker Build Notes

The API service uses the **repo root** as its build context (not `apps/vps-admin-api/`) because the Dockerfile needs access to `packages/python/vsa-common/`. The Dockerfile mirrors the repo layout at `/workspace/apps/vps-admin-api/` so that `pyproject.toml` relative paths resolve correctly. A root `.dockerignore` keeps the build context small.

## Database

5 tables managed by Alembic (`apps/vps-admin-api/alembic/`):
- `vps_nodes` — registered VPS instances
- `domains` — domain registry
- `certificates` — TLS certificate tracking
- `audit_logs` — audit trail (indexed on timestamp, actor, action)
- `container_snapshots` — container state snapshots from agents

Run migrations: `docker compose exec dashboard-api python -c "from alembic.config import Config; from alembic import command; import os; cfg = Config('/app/alembic.ini'); cfg.set_main_option('sqlalchemy.url', os.environ['VSA_DATABASE_URL']); command.upgrade(cfg, 'head')"`

## NGINX Routing

The dashboard vhost at `stacks/reverse-proxy/nginx/conf.d/dashboard.flowbiz.ai.conf` routes:
- `/api/*` to `dashboard-api:8000`
- `/*` to `dashboard-ui:3000`

Protected by HTTP Basic Auth and security headers (HSTS, X-Frame-Options, CSP).
