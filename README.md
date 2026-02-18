# FlowBiz VPS Admin Suite (VSA)

Monorepo for managing multi-tenant hosting on Infomaniak VPS (primary) and Kamatera (legacy). Orchestrates Docker Compose stacks for AI apps (Dify, n8n, local LLMs) and customer websites behind an NGINX reverse proxy with Let's Encrypt SSL automation.

## Components

| Component | Location | Description |
|-----------|----------|-------------|
| **`vsa` CLI** | `apps/vps-admin-cli/` | Python Typer CLI for all infrastructure operations |
| **Dashboard API** | `apps/vps-admin-api/` | FastAPI backend — containers, domains, certs, traffic, audit |
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

## Dashboard

Live at `https://dashboard.flowbiz.ai/` (HTTP Basic Auth).

**7 pages:**
- **Overview** — system summary
- **Containers** — Docker container status (live from Docker SDK)
- **Domains** — provisioned domain list
- **Certificates** — SSL cert expiry with days remaining and status (live from disk)
- **Traffic** — per-domain analytics with stats cards, breakdown table, raw logs (live from Loki)
- **Audit** — infrastructure operation log with pagination (reads from local SQLite + PostgreSQL, merged)
- **VPS** — node information

## CLI Commands

```bash
# Site management
vsa site provision --domain X --container Y --port Z
vsa site provision --domain X --port Z --detect --external-port Z
vsa site unprovision --domain X [--keep-container] [--keep-cert] [-y]
vsa site list

# Multipoint provisioning (multiple backends on one domain)
# Routes different URL paths to different containers behind a single domain
vsa site provision --domain promoflash.flowbiz.ai \
  --route /=promoflash-frontend:80 \
  --route /api/=promoflash-pocketbase:8090 \
  --route /_/=promoflash-pocketbase:8090
# Result: / → frontend, /api/* → PocketBase API, /_/* → PocketBase admin UI

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

# VPS fleet management
vsa vps list                                        # List all VPS nodes
vsa vps add --id vps-02 --hostname X --ip Y         # Pre-register a VPS
vsa vps remove VPS_ID [-y]                          # Remove VPS + all data
```

## Repository Layout

```
packages/python/vsa-common/    Shared Pydantic models and constants
apps/
  vps-admin-cli/               vsa CLI (Typer + Jinja2 + bcrypt + audit + VPS mgmt)
  vps-admin-api/               Dashboard API (FastAPI + SQLAlchemy + PostgreSQL + Loki + SQLite)
  vps-admin-ui/                Dashboard UI (Next.js 14 + Tailwind + React Query)
stacks/
  reverse-proxy/               NGINX 1.25 + Certbot + NGINX Reloader (auto-renewal)
  dashboard/                   Dashboard stack (API + UI + PostgreSQL)
  dify/                        Dify LLM platform
  observability/               Grafana 10.4, Loki 3.0, Promtail, Prometheus 2.53, cAdvisor
  llm-gateway/                 LLM backend routing (placeholder)
  templates/                   Reusable compose snippets
infra/
  scripts/                     Legacy bash scripts (superseded by CLI)
  systemd/                     systemd units for VSA agent
docs/
  architecture.md              Full architecture documentation
  ADRs/                        Architecture decision records
  runbooks/                    Operational runbooks
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for full architecture documentation with data flow diagrams.

### Key Design Decisions

- **Traffic analytics** query Loki directly via LogQL metric queries — no dependency on PostgreSQL for traffic data
- **Certificate monitoring** reads Let's Encrypt cert files from disk via the `cryptography` library — always live, never stale
- **NGINX per-domain JSON logging** enables structured traffic analytics (Promtail extracts domain/method/status labels)
- **Jinja2 templates** for vhost generation (replaces fragile sed-based substitution)
- **Dual-write audit logging** to JSONL (Promtail → Loki → Grafana) + SQLite (local queries + direct dashboard reads on hub)
- **Hub-and-agent** model for multi-VPS — dashboard on VPS-01, agents sync via systemd timer, full reconciliation (stale entries auto-cleaned)
- **VPS fleet management** — `vsa vps list/add/remove` for managing multi-VPS nodes from the CLI
- **Automated cert renewal** — Certbot container renews every 12h, NGINX Reloader sidecar reloads every 6h
- **Comprehensive unprovision** — 6-step domain cleanup with shared container detection
- **Reboot resilience** — all containers have `restart: unless-stopped`, Docker daemon enabled on boot

### Networking

All stacks join the shared `flowbiz_ext` Docker network for reverse proxy access. Each stack has its own internal network. No database ports are exposed publicly.

### Storage

**Two-disk layout:**
- **Root `/`** — OS, configs, NGINX vhosts/certs/logs, `.env` files
- **`/var/lib/docker` (dedicated 246G disk)** — Docker named volumes for heavy data

Observability data (Prometheus, Loki, Grafana) uses Docker named volumes to avoid filling the root disk. Prometheus is capped at 15d/1GB, Loki retains 30 days of logs.

```
/srv/<tenant>/<app>/
  data/      Application data, DB volumes
  env/       .env files (chmod 640, never committed)
  logs/      Application logs
```

## Development

```bash
# Install CLI dependencies
cd apps/vps-admin-cli && uv sync

# Run tests (30+ unit tests)
uv run pytest -q

# Lint
uv run ruff check .

# Run all quality checks
make lint
make test
```

## Deployment

```bash
# Dashboard (already deployed on VPS-01)
cd stacks/dashboard
docker compose up -d --build     # PostgreSQL + API + UI

# API-only rebuild (faster)
docker compose up -d --build dashboard-api

# UI-only rebuild
docker compose up -d --build dashboard-ui

# Deploy observability
cd stacks/observability
docker compose up -d

# Install cert monitoring cron (optional, certbot container handles renewal)
vsa cert install-cron
```

> **Note:** Certificate renewal is automatic — the certbot container checks every 12h and the NGINX reloader picks up renewed certs every 6h. The cron job is an optional extra safety net with logging.

## Conventions

- **Python**: 3.11+, uv, Ruff, pytest, Pydantic
- **Node**: 20 LTS, pnpm, ESLint, Prettier, Next.js + Tailwind
- **Docker**: Multi-stage builds, non-root users, HEALTHCHECK required, < 300MB, `restart: unless-stopped` on all containers
- **Git**: Conventional commits, trunk-based, SemVer tags
- **Every CLI command**: Must use `audit()` context manager
- **Every stack**: Must have compose.yml, .env.example, README.md, healthchecks

## Documentation

- [Architecture (high-level)](docs/architecture.md)
- [Low-Level Design](docs/low-level-design.md)
- [ADR-001: CLI Replaces Bash Scripts](docs/ADRs/001-cli-replaces-bash-scripts.md)
- [ADR-002: Jinja2 Vhost Templates](docs/ADRs/002-jinja2-vhost-templates.md)
- [ADR-003: Dual-Write Audit Logging](docs/ADRs/003-dual-write-audit-logging.md)
- [ADR-004: Hub-and-Agent Multi-VPS](docs/ADRs/004-hub-and-agent-multi-vps.md)
- [Runbook: Provision a Site](docs/runbooks/provision_site.md)
- [Runbook: Restore](docs/runbooks/restore.md)
