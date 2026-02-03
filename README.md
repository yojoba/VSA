# FlowBiz VPS Admin Suite (VSA)

Monorepo for managing multi-tenant hosting on Infomaniak VPS (primary) and Kamatera (legacy). Orchestrates Docker Compose stacks for AI apps (Dify, n8n, local LLMs) and customer websites behind an NGINX reverse proxy with Let's Encrypt SSL automation.

## Components

| Component | Location | Description |
|-----------|----------|-------------|
| **`vsa` CLI** | `apps/vps-admin-cli/` | Python Typer CLI for all infrastructure operations |
| **Dashboard API** | `apps/vps-admin-api/` | FastAPI backend — containers, domains, certs, audit logs |
| **Dashboard UI** | `apps/vps-admin-ui/` | Next.js 14 frontend at `dashboard.flowbiz.ai` |
| **Shared library** | `packages/python/vsa-common/` | Pydantic models and config shared by CLI and API |
| **Stacks** | `stacks/` | Docker Compose stacks (reverse-proxy, dashboard, dify, observability) |

## Quick Start

```bash
# Install the CLI
cd apps/vps-admin-cli && uv tool install .

# Bootstrap a fresh VPS
vsa bootstrap

# Bring up the reverse proxy
vsa stack up reverse-proxy

# Provision a site
vsa site provision --domain example.com --container web-1 --port 3000

# Check certificates
vsa cert status
```

## CLI Commands

```bash
# Site management
vsa site provision --domain X --container Y --port Z
vsa site provision --domain X --port Z --detect --external-port Z
vsa site unprovision --domain X
vsa site list

# SSL certificates
vsa cert issue --domain X
vsa cert renew
vsa cert status
vsa cert install-cron

# HTTP Basic Auth (bcrypt)
vsa auth add --domain X --user Y
vsa auth remove --domain X
vsa auth list

# NGINX vhosts
vsa vhost sync
vsa vhost list
vsa vhost show DOMAIN

# Docker Compose stacks
vsa stack new NAME
vsa stack up/down/logs/ps NAME

# Multi-VPS agent
vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token XXX
vsa agent start
vsa agent status
```

## Repository Layout

```
packages/python/vsa-common/    Shared Pydantic models and constants
apps/
  vps-admin-cli/               vsa CLI (Typer + Jinja2 + bcrypt + audit)
  vps-admin-api/               Dashboard API (FastAPI + SQLAlchemy + PostgreSQL)
  vps-admin-ui/                Dashboard UI (Next.js 14 + Tailwind + React Query)
stacks/
  reverse-proxy/               NGINX 1.25 + Certbot (14 live vhosts)
  dashboard/                   Dashboard stack (API + UI + PostgreSQL)
  dify/                        Dify LLM platform
  observability/               Grafana, Loki, Promtail, Prometheus, cAdvisor
  llm-gateway/                 LLM backend routing (placeholder)
  templates/                   Reusable compose snippets
infra/
  scripts/                     Legacy bash scripts (superseded by CLI)
  scripts/_deprecated/         One-off migration scripts
  systemd/                     systemd units for VSA agent
docs/
  ADRs/                        Architecture decision records
  runbooks/                    Operational runbooks
```

## Architecture

### Networking

All stacks join the shared `flowbiz_ext` Docker network for reverse proxy access. Each stack has its own internal network. No database ports are exposed publicly.

### Data Paths

```
/srv/<tenant>/<app>/
  data/      Application data, DB volumes
  env/       .env files (chmod 640, never committed)
  logs/      Application logs
```

### NGINX Vhost Generation

The CLI uses **Jinja2 templates** to generate NGINX configs (replacing fragile sed-based substitution). Every vhost includes security headers (HSTS, X-Frame-Options DENY, CSP) and rate limiting.

### Audit Logging

Every CLI operation writes to:
- `/var/log/vsa/audit.jsonl` — scraped by Promtail into Loki for Grafana
- `/var/lib/vsa/audit.db` — SQLite for local queries and dashboard sync

### Multi-VPS (Hub-and-Agent)

VPS-01 hosts the dashboard (hub). Additional VPS nodes run `vsa agent` via systemd timer, syncing heartbeats, container state, cert status, and audit events.

## Development

```bash
# Install CLI dependencies
cd apps/vps-admin-cli && uv sync

# Run tests (30 unit tests)
uv run pytest -q

# Lint
uv run ruff check .

# Run all quality checks
make lint
make test
```

## Deployment

The dashboard is deployed and running at `https://dashboard.flowbiz.ai/` (HTTP Basic Auth).

```bash
# Dashboard (already deployed on VPS-01)
cd stacks/dashboard
docker compose up -d --build     # PostgreSQL + API + UI
# .env is symlinked from /srv/flowbiz/dashboard/env/.env
# TLS cert via Let's Encrypt, NGINX reverse proxy configured

# Deploy observability
cd stacks/observability
docker compose up -d

# Install cert monitoring cron
vsa cert install-cron
```

## Conventions

- **Python**: 3.11+, uv, Ruff, pytest, Pydantic
- **Node**: 20 LTS, pnpm, ESLint, Prettier, Next.js + Tailwind
- **Docker**: Multi-stage builds, non-root users, HEALTHCHECK required, < 300MB
- **Git**: Conventional commits, trunk-based, SemVer tags
- **Every CLI command**: Must use `audit()` context manager
- **Every stack**: Must have compose.yml, .env.example, README.md, healthchecks

## Documentation

- [ADR-001: CLI Replaces Bash Scripts](docs/ADRs/001-cli-replaces-bash-scripts.md)
- [ADR-002: Jinja2 Vhost Templates](docs/ADRs/002-jinja2-vhost-templates.md)
- [ADR-003: Dual-Write Audit Logging](docs/ADRs/003-dual-write-audit-logging.md)
- [ADR-004: Hub-and-Agent Multi-VPS](docs/ADRs/004-hub-and-agent-multi-vps.md)
- [Runbook: Provision a Site](docs/runbooks/provision_site.md)
- [Runbook: Restore](docs/runbooks/restore.md)
