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

**9 pages:**
- **Overview** — system summary
- **Fleet Health** — drift report cross-checking intent (assignments) vs observed state (60s refresh)
- **Containers** — Docker container status (agent-synced, every VPS)
- **Domains** — provisioned domain list per VPS
- **Certificates** — SSL cert expiry with days remaining + SANs (agent-synced)
- **Assignments** — primary + warm-standby VPS for each domain
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
vsa cert issue --domain X                                 # HTTP-01 webroot (default)
vsa cert issue --domain X --challenge dns-cloudflare      # DNS-01 via Cloudflare API token
vsa cert issue --domain X --no-www                        # Skip www. SAN (technical subdomains)
vsa cert renew                                            # Renew all expiring certs
vsa cert status                                           # Live cert list (host LE store)
vsa cert health                                           # Diagnostic: broken symlinks, missing
                                                          # accounts, expiring certs (exits 1 on any
                                                          # critical finding — cron-friendly)
vsa cert install-cron                                     # Install daily renewal cron

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

# VPS fleet management (registry of nodes)
vsa vps list                                        # List all VPS nodes
vsa vps add --id vps-02 --hostname X --ip Y         # Pre-register a VPS
vsa vps remove VPS_ID [-y]                          # Remove VPS + all data

# Site standby (multi-VPS) — vps-X serves while vps-Y stays warm-ready
# (vps-Y has the vhost + cert via DNS-01 but no container)
vsa site provision --domain X --container c --port p --standby \
                                                   # On the standby host:
                                                   # vhost only, cert via DNS-01,
                                                   # no Docker network attach
```

## Multi-VPS Fleet Operations

Run from the hub (vps-01) — needs `VSA_HUB_URL` + `VSA_HUB_AUTH=admin:<pass>`
in `/etc/vsa/agent.env`. Subcommands hit `/api/...` on the hub and (for `exec`
+ orchestrators) push commands through the agent queue, which each VPS picks
up on its next 30s sync tick.

```bash
# Domain assignment registry — declares primary + warm-standbys per domain
vsa fleet assign --domain lokalflash.ch --primary vps-02 --standbys vps-03 \
                 --notes "LokalFlash prod (HTTP-01 on primary, DNS-01 on standby)"
vsa fleet list                                  # Table of all assignments
vsa fleet show DOMAIN                           # Single-row lookup (404 if none)
vsa fleet remove DOMAIN [-y]                    # Drop the assignment row only
                                                # (doesn't touch agent-synced state)
vsa fleet backfill [--dry-run]                  # Auto-create default assignments
                                                # for unassigned single-host domains

# Run any vsa command on a remote VPS via the hub command queue
vsa fleet exec --vps vps-03 -- cert health
vsa fleet exec --vps vps-02 -- vhost sync
vsa fleet exec --vps vps-03 --timeout 300 -- cert renew

# Convenience wrappers — one-shot common ops
vsa fleet vhost-sync --vps vps-02
vsa fleet cert-renew --vps vps-02
vsa fleet cert-health --vps vps-03              # One VPS
vsa fleet cert-health --all                     # Every VPS in the registry

# Full multi-VPS site provisioning (HTTP-01 on primary, DNS-01 on each standby)
vsa fleet site-provision \
  --domain app.lokalflash.ch \
  --primary vps-02 --standbys vps-03 \
  --container lokalflash-frontend --port 80 \
  --no-www
                                                # Runs `site provision` on primary,
                                                # then `site provision … --standby` on
                                                # each standby (DNS-01 cert, no
                                                # container start), then writes the
                                                # assignment row.

# Drift detection — flag mismatches between intent and observed state
vsa fleet drift                                 # Critical+warning only (exits 1 if any critical)
vsa fleet drift --show-info                     # Include "info" findings (orphan domains, etc.)
```

### Multi-VPS prerequisites

- **On the hub** (vps-01): `VSA_HUB_URL` + `VSA_HUB_AUTH` exported via
  `/etc/vsa/agent.env` (loaded by both the systemd agent and any
  interactive `sudo --preserve-env=PATH bash -c "source /etc/vsa/agent.env; …"`).
- **On any VPS that issues certs via DNS-01**: drop a Cloudflare API
  token at `/srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini`
  (mode 0600), and enable the `compose.dns-cloudflare.yml` override via
  `COMPOSE_FILE=compose.yml:compose.dns-cloudflare.yml` in
  `stacks/reverse-proxy/.env`. See [docs/runbooks/dns01_cloudflare.md](docs/runbooks/dns01_cloudflare.md).

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
- **Hub→agent execution channel** — `vsa fleet exec --vps X -- …` enqueues commands for the target agent to pick up on its next tick (see [ADR-006](docs/ADRs/006-hub-to-agent-execution.md))
- **Domain assignments registry** — `domain_assignments` table records which VPS is primary + which are warm standbys per domain; cross-checked by drift detection
- **SAN-aware drift detection** — `/api/fleet/drift` recognises that one cert can cover apex + www; agent populates `certificates.sans` from `openssl x509 -ext subjectAltName`
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
- [ADR-005: Multi-VPS-aware Dashboard](docs/ADRs/005-multi-vps-aware-dashboard.md)
- [ADR-006: Hub→Agent Execution Channel](docs/ADRs/006-hub-to-agent-execution.md)
- [Runbook: Provision a Site](docs/runbooks/provision_site.md)
- [Runbook: DNS-01 Cloudflare Cert Auto-Renewal](docs/runbooks/dns01_cloudflare.md)
- [Runbook: Observability Agent](docs/runbooks/observability_agent.md)
- [Runbook: Fleet Access](docs/runbooks/fleet_access.md)
- [Runbook: Restore](docs/runbooks/restore.md)
