# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FlowBiz VPS Admin Suite (VSA)** — A monorepo for managing multi-tenant hosting on Infomaniak VPS (primary) and Kamatera (legacy). Orchestrates Docker Compose stacks for AI apps (Dify, n8n, local LLMs) and customer websites behind an NGINX reverse proxy with Let's Encrypt SSL automation.

The project has three main components:
1. **`vsa` CLI** — Python/Typer CLI replacing all bash scripts with audited, tested tooling
2. **Dashboard** — FastAPI + Next.js centralized management at `dashboard.flowbiz.ai`
3. **Observability** — Grafana/Loki/Promtail/Prometheus with audit log pipeline and traffic analytics

## Build & Development Commands

```bash
# Stack lifecycle
make up                    # docker compose up -d --build
make down                  # docker compose down
make logs                  # docker compose logs -f --tail=200
make ps                    # docker compose ps

# Code quality
make lint                  # hadolint, ruff check, eslint
make format                # ruff format, prettier
make test                  # pytest (CLI unit tests)

# VSA CLI (primary tool for all infrastructure operations)
vsa site provision --domain X --container Y --port Z
vsa site provision --domain X --port Z --detect --external-port Z
vsa site unprovision --domain X [--keep-container] [--keep-cert] [-y]
vsa site list
vsa auth add --domain X --user Y
vsa auth remove --domain X
vsa cert renew
vsa cert status
vsa cert install-cron
vsa vhost sync
vsa stack new NAME
vsa stack up NAME
vsa bootstrap

# VPS fleet management
vsa vps list
vsa vps add --id vps-02 --hostname myserver --ip 1.2.3.4
vsa vps remove VPS_ID [-y]

# Make targets delegate to CLI
make provision-container domain=<domain> port=<port> [nowww=true]
make unprovision-container domain=<domain>
make sync-vhosts
make check-certs
make add-basic-auth domain=<domain> user=<user> password=<password>
make remove-basic-auth domain=<domain>
```

Per-stack Makefiles exist in `stacks/reverse-proxy/`, `stacks/dashboard/`, etc.

## Architecture

See `docs/architecture.md` for full architecture documentation with diagrams.

### Monorepo Layout

- **`packages/python/vsa-common/`** — Shared Pydantic models and constants (AuditEvent, SiteConfig, VsaConfig)
- **`apps/`** — Application code
  - `vps-admin-cli/` — `vsa` CLI (Typer + Jinja2 + bcrypt + audit logging)
  - `vps-admin-api/` — Dashboard API (FastAPI + SQLAlchemy + PostgreSQL + Loki)
  - `vps-admin-ui/` — Dashboard frontend (Next.js 14 + Tailwind + React Query)
- **`stacks/`** — Docker Compose stacks, each self-contained with `compose.yml`, `.env.example`, and `README.md`
  - `reverse-proxy/` — NGINX 1.25 + Certbot (core infrastructure)
  - `dashboard/` — Dashboard stack (API + UI + PostgreSQL)
  - `dify/` — LLM platform
  - `observability/` — Grafana 10.4, Loki 3.0, Promtail 3.0, Prometheus 2.53, Node Exporter, cAdvisor
  - `llm-gateway/` — Placeholder for LLM backend routing
  - `templates/` — Reusable compose snippets
- **`infra/`** — Infrastructure automation
  - `scripts/` — Legacy bash scripts (being superseded by CLI)
  - `scripts/_deprecated/` — One-off migration scripts
  - `systemd/` — systemd units for VSA agent
- **`docs/`** — Architecture docs, runbooks, and ADRs

### Dashboard API

9 routers: `agent`, `audit_logs`, `certs`, `containers`, `domains`, `stacks`, `traffic`, `vps`

Key services:
- **Loki client** (`services/loki.py`) — queries Loki for raw traffic logs and aggregated stats via LogQL metric queries (`count_over_time`, `sum_over_time`, `avg_over_time` with `unwrap`)
- **Certificate scanner** (`routers/certs.py`) — reads Let's Encrypt cert files from disk via `cryptography` library, returns live expiry dates and status
- **Audit logs** (`routers/audit_logs.py`) — reads from local SQLite (`/var/lib/vsa/audit.db`, mounted in container) for hub events, merges with PostgreSQL for remote agent events, deduplicates by (timestamp, actor, action, target)

### Dashboard UI

7 pages: Overview (`/`), Containers, Domains, Certificates, Audit, Traffic, VPS

### Traffic Analytics Pipeline

```
NGINX (json_detailed log format per domain)
  → Promtail (scrapes *.access.json, extracts domain/method/status labels)
    → Loki (stores with job="nginx-domain-access")
      → Dashboard API (LogQL metric queries for aggregated stats)
        → Dashboard UI (stats cards, per-domain breakdown, raw logs table)
```

The traffic stats endpoint queries Loki directly using LogQL aggregation — no dependency on the agent sync or PostgreSQL for traffic data.

### Certificate Monitoring

The `/api/certs` endpoint reads Let's Encrypt certificates directly from disk (`/etc/letsencrypt/live/*/fullchain.pem`) using the `cryptography` library. Returns live expiry dates, days remaining, and status (`valid`, `warning` <=30d, `critical` <=14d, `expired`).

### VSA CLI Architecture

```
apps/vps-admin-cli/src/vsa/
├── cli.py              # Root Typer app
├── config.py           # VsaConfig singleton (paths from VSA_ROOT env)
├── audit.py            # Dual-write JSONL + SQLite audit logger
├── errors.py           # Custom exceptions
├── commands/           # CLI command groups (site, cert, auth, stack, vhost, vps, bootstrap, agent)
├── services/           # Business logic (docker, nginx, certbot, htpasswd, vhost_renderer, network, agent_sync)
├── templates/          # Jinja2 NGINX vhost templates
└── models/             # Re-exported Pydantic models
```

Key design decisions:
- **Jinja2 templates** for vhost generation (replaces sed placeholder substitution)
- **bcrypt** for htpasswd entries (replaces APR1/MD5)
- **`nginx -t` before reload** (validates config before applying)
- **Structured audit logging** to `/var/log/vsa/audit.jsonl` + `/var/lib/vsa/audit.db`
- **Pydantic config** with `VSA_ROOT` env var (no hardcoded paths)

### Dashboard Docker Build

The API Dockerfile uses the **repo root** as its build context (set in `stacks/dashboard/compose.yml`) so it can access `packages/python/vsa-common/`. The Dockerfile mirrors the repo layout at `/workspace/` to keep `pyproject.toml` relative paths working. A root `.dockerignore` excludes `.git`, `node_modules`, `stacks/`, etc. to keep the build context small. The runtime stage uses `PYTHONPATH=/app/src` since `uv sync` creates editable installs with builder-stage paths.

### Networking Model

All stacks join the shared `flowbiz_ext` Docker network for reverse proxy access. Each stack also has its own internal network (e.g., `dashboard-net`) for inter-service communication. No database ports are exposed publicly.

### Data Path Convention

```
/srv/<tenant>/<app>/
├── data/    # Application data, DB volumes
├── env/     # .env files (chmod 640, never committed)
├── logs/    # Application logs
└── compose/ # Optional compose overrides
```

### Reverse Proxy & TLS

NGINX vhost files live in `stacks/reverse-proxy/nginx/conf.d/<domain>.conf`. Every vhost includes `security_headers.conf` (HSTS, X-Frame-Options DENY, CSP) and rate limiting. Per-domain JSON access logs are written to `/var/log/nginx/domains/<domain>.access.json` using the `json_detailed` log format. Certbot handles Let's Encrypt via HTTP-01 (webroot). The `vsa site provision` command handles the full workflow: network attach, HTTP vhost, cert issuance, HTTPS vhost, nginx reload.

### Certificate Auto-Renewal

The reverse-proxy stack includes three services for automated TLS:
- **`reverse-proxy-certbot`** — runs `certbot renew` every 12 hours, automatically renews any cert within 30 days of expiry
- **`reverse-proxy-reloader`** — reloads NGINX every 6 hours via `docker exec` to pick up renewed certs (uses `docker:27-cli` image with Docker socket access)
- **`reverse-proxy-nginx`** — serves traffic, mounts `/etc/letsencrypt` for cert access

All renewal configs live in `/srv/flowbiz/reverse-proxy/letsencrypt/renewal/<domain>.conf`. Each domain provisioned via `vsa site provision` automatically gets a renewal config.

### Site Unprovision

`vsa site unprovision --domain X` performs comprehensive cleanup (6 steps):
1. Remove vhost config (repo + mount directories)
2. Remove auth/htpasswd files
3. Delete Let's Encrypt certificate (via certbot)
4. Remove per-domain access log files
5. Stop and remove container (with shared container detection)
6. Reload NGINX

**Shared container detection:** Before removing a container, scans all vhost configs to find other domains pointing to the same upstream container. If shared, warns the user and asks whether to keep or remove the container. Flags: `--keep-container`, `--keep-cert`, `--yes/-y` for non-interactive mode.

### Multi-VPS Strategy (Hub-and-Agent)

VPS-01 hosts the dashboard (hub). Additional VPS nodes run the CLI with `vsa agent` subcommand:
- `vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token XXX`
- `vsa agent start` (via systemd timer, every 30s)
- Sends heartbeats, container snapshots, cert status, traffic stats, and audit events to hub

**VPS fleet management** via `vsa vps`:
- `vsa vps list` — list all registered VPS nodes (table with ID, hostname, IP, status, last seen)
- `vsa vps add --id vps-02 --hostname X --ip Y` — pre-register a VPS node in the dashboard
- `vsa vps remove VPS_ID [-y]` — remove a VPS and all associated data (domains, snapshots, traffic)

**Adding a new VPS to the system:**
1. On the hub: `vsa vps add --id vps-02 --hostname newserver --ip 1.2.3.4`
2. On the new VPS: install CLI, then `vsa agent register --hub-url ... --token ...`
3. On the new VPS: `vsa agent start` (systemd timer takes over, syncs every 30s)

### Agent Sync Reconciliation

Agent sync endpoints perform **full reconciliation**, not append-only:
- **`domains-sync`** — upserts domains from the payload, then deletes DB entries for that VPS that are no longer in the vhost directory (stale domains removed automatically after unprovision)
- **`certs-sync`** — same pattern: upserts current certs, deletes stale entries no longer reported
- **`containers-sync`** — full replacement: deletes all snapshots for the VPS, re-inserts current state
- **`DELETE /api/agent/vps/{vps_id}`** — removes a VPS node and all associated data (domains, snapshots, traffic stats)

## Conventions

- **Language defaults:** Python 3.11+, Node 20 LTS
- **Python tooling:** `uv` (preferred), Ruff for lint/format, pytest, FastAPI + Uvicorn, Typer for CLIs, Pydantic for config
- **Node tooling:** pnpm, ESLint, Prettier, Next.js + Tailwind
- **Docker:** Multi-stage builds, non-root users, slim base images, `HEALTHCHECK` required, images < 300MB, `restart: unless-stopped` on **every** container (verified reboot-safe)
- **Git:** Trunk-based development, conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), SemVer tags
- **Bash scripts:** Use `set -Eeuo pipefail`, idempotent, clear logging with `echo "[step] ..."`
- **Indentation:** 2 spaces default, 4 spaces for Python, tabs for Makefiles (see `.editorconfig`)
- **Audit logging:** Every infrastructure operation must use the `audit()` context manager

## When Generating New Stacks

1. Create `stacks/<name>/compose.yml`, `.env.example`, `README.md`, and optionally `Makefile`
2. Include healthchecks, restart policies, volumes, and network configuration
3. Networks: join `flowbiz_ext` + create local `<name>-net`
4. Use `vsa stack new NAME` to scaffold from template
5. Provision with `vsa site provision --domain X --container Y --port Z`

## When Adding CLI Commands

1. Create command module in `apps/vps-admin-cli/src/vsa/commands/`
2. Register in `cli.py` via `app.add_typer()` or `app.command()`
3. Wrap operations with `audit()` context manager
4. Add unit tests in `apps/vps-admin-cli/tests/`
5. Run tests: `cd apps/vps-admin-cli && uv run pytest -q`

## When Adding API Endpoints

1. Create or extend a router in `apps/vps-admin-api/src/vsa_api/routers/`
2. Register in `main.py` via `app.include_router(router, prefix="/api")`
3. For live data, prefer reading from disk or Loki over PostgreSQL when possible (avoids stale data from agent sync)
4. Add TypeScript types in `apps/vps-admin-ui/src/lib/api.ts`
5. Rebuild: `cd stacks/dashboard && docker compose up -d --build dashboard-api`

## Keeping Rules in Sync

After any significant change (new stacks, architectural shifts, new tooling, changed conventions), update **all three**:
- **`CLAUDE.md`** (this file) — for Claude Code
- **`.cursor/rules/vsa.mdc`** — for Cursor IDE
- **`docs/architecture.md`** — for human reference

This ensures all AI assistants and developers stay aligned with the current state of the project.

## Implementation Status

All 7 phases of the VSA modernization plan have been implemented and committed, plus additional traffic analytics and certificate monitoring features.

### Completed
- **Phase 1 — Foundation:** Shared library (`packages/python/vsa-common/`), CLI skeleton, Jinja2 vhost renderer, bcrypt htpasswd service, 30 unit tests
- **Phase 2 — CLI Core:** All command modules (site, cert, auth, stack, vhost, vps, bootstrap, agent) with audit logging
- **Phase 3 — Observability:** Loki 90-day retention, Promtail audit scrape pipeline, NGINX rate limiting zones
- **Phase 4 — Dashboard Backend:** FastAPI + SQLAlchemy async + PostgreSQL, 9 API routers, Alembic migrations (6 tables), Docker SDK integration, multi-stage Dockerfile (repo root build context with `PYTHONPATH`)
- **Phase 5 — Dashboard Frontend:** Next.js 14 + Tailwind + React Query, 7 pages (overview, containers, domains, certs, audit, traffic, VPS), sidebar nav, status badges, standalone output mode
- **Phase 6 — Multi-VPS Agent:** systemd service + timer units, agent register/start/status commands, traffic stats sync
- **Phase 7 — Cleanup:** Deprecated one-off scripts, updated Makefile to delegate to CLI, 4 ADRs, updated runbooks, consolidated root README
- **Phase 8 — Traffic Analytics:** Per-domain NGINX JSON logging, Promtail `nginx-domain-access` scrape job, Loki LogQL metric queries for aggregated stats, traffic dashboard page with stats cards, per-domain breakdown, raw logs table
- **Phase 9 — Live Certificate Monitoring:** `cryptography` library for parsing cert files from disk, live expiry dates/status/days remaining, color-coded status badges (valid/warning/critical/expired)
- **Phase 10 — Certificate Auto-Renewal:** Certbot container runs `certbot renew` every 12h, nginx-reloader sidecar reloads NGINX every 6h via Docker socket, replaces broken `deploy-hook` approach
- **Phase 11 — Comprehensive Unprovision:** `vsa site unprovision` performs 6-step cleanup (vhost, auth, cert, logs, container, nginx reload) with shared container detection and interactive prompts
- **Phase 12 — Reboot Resilience:** All containers have `restart: unless-stopped`, Docker daemon enabled on boot, verified all 36 containers survive unattended reboot
- **Phase 13 — Sync Reconciliation & VPS Fleet Management:** Agent sync now performs full reconciliation (stale domains/certs auto-removed after unprovision), audit logs endpoint reads directly from local SQLite (no agent sync dependency for hub events), new `vsa vps` CLI command group (list/add/remove), `DELETE /api/agent/vps/{vps_id}` endpoint

### Deployed
- **Dashboard live** at `https://dashboard.flowbiz.ai/` — API + UI + PostgreSQL running on VPS-01, TLS via Let's Encrypt (auto-renew), HTTP Basic Auth (`admin`), NGINX reverse proxy routing `/api/*` to API and `/*` to UI
- **Traffic analytics live** — all domains showing real-time traffic stats from Loki
- **Certificate monitoring live** — real expiry dates read from Let's Encrypt cert files on disk
- **CLI installed** on VPS-01 at `~/.local/bin/vsa`
- **Frontend dependencies** installed (pnpm lockfile generated)
- **Alembic migrations:** `0001_initial_tables.py` (5 tables), `0002_traffic_stats.py` (traffic_stats table)

### Pending / Known Issues
- **GitHub Actions CI** (`.github/workflows/ci.yml`) — file exists locally but was removed from git because the GitHub PAT lacks the `workflow` scope. To re-add: update PAT with `workflow` scope, then `git add .github/workflows/ci.yml && git commit -m "ci: add GitHub Actions pipeline" && git push`
- **No `.env` files committed** — by design; `.env.example` files provide templates
- **NGINX healthcheck** — `reverse-proxy-nginx` shows `unhealthy` because the healthcheck calls `http://localhost/healthz` but there's no default server block for `localhost`; doesn't affect functionality

### Test Status
- 30+ CLI unit tests passing: `cd apps/vps-admin-cli && uv run pytest -q`
- Tests cover: Pydantic models, VsaConfig, bcrypt htpasswd, Jinja2 vhost rendering, audit logging (JSONL + SQLite), agent sync traffic collection
- `uv` must be on PATH: installed at `~/.local/bin/uv` via `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Quality Gates

Do not merge if:
- Missing `.env.example` (with placeholder-only values)
- No healthcheck in Dockerfile/compose
- No README for a new stack
- DB ports exposed to the internet
- NGINX vhost missing security headers/HSTS
- CLI command missing audit logging
- Tests failing (`cd apps/vps-admin-cli && uv run pytest -q`)
