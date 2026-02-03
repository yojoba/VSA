# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FlowBiz VPS Admin Suite (VSA)** — A monorepo for managing multi-tenant hosting on Infomaniak VPS (primary) and Kamatera (legacy). Orchestrates Docker Compose stacks for AI apps (Dify, n8n, local LLMs) and customer websites behind an NGINX reverse proxy with Let's Encrypt SSL automation.

The project has three main components:
1. **`vsa` CLI** — Python/Typer CLI replacing all bash scripts with audited, tested tooling
2. **Dashboard** — FastAPI + Next.js centralized management at `dashboard.flowbiz.ai`
3. **Observability** — Grafana/Loki/Promtail/Prometheus with audit log pipeline

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
vsa site unprovision --domain X
vsa auth add --domain X --user Y
vsa auth remove --domain X
vsa cert renew
vsa cert status
vsa cert install-cron
vsa vhost sync
vsa stack new NAME
vsa stack up NAME
vsa bootstrap

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

### Monorepo Layout

- **`packages/python/vsa-common/`** — Shared Pydantic models and constants (AuditEvent, SiteConfig, VsaConfig)
- **`apps/`** — Application code
  - `vps-admin-cli/` — `vsa` CLI (Typer + Jinja2 + bcrypt + audit logging)
  - `vps-admin-api/` — Dashboard API (FastAPI + SQLAlchemy + PostgreSQL)
  - `vps-admin-ui/` — Dashboard frontend (Next.js 14 + Tailwind + React Query)
- **`stacks/`** — Docker Compose stacks, each self-contained with `compose.yml`, `.env.example`, and `README.md`
  - `reverse-proxy/` — NGINX 1.25 + Certbot (core infrastructure)
  - `dashboard/` — Dashboard stack (API + UI + PostgreSQL)
  - `dify/` — LLM platform
  - `observability/` — Grafana, Loki, Promtail, Prometheus, Node Exporter, cAdvisor
  - `llm-gateway/` — Placeholder for LLM backend routing
  - `templates/` — Reusable compose snippets
- **`infra/`** — Infrastructure automation
  - `scripts/` — Legacy bash scripts (being superseded by CLI)
  - `scripts/_deprecated/` — One-off migration scripts
  - `systemd/` — systemd units for VSA agent
- **`docs/`** — Runbooks and ADRs

### VSA CLI Architecture

```
apps/vps-admin-cli/src/vsa/
├── cli.py              # Root Typer app
├── config.py           # VsaConfig singleton (paths from VSA_ROOT env)
├── audit.py            # Dual-write JSONL + SQLite audit logger
├── errors.py           # Custom exceptions
├── commands/           # CLI command groups (site, cert, auth, stack, vhost, bootstrap, agent)
├── services/           # Business logic (docker, nginx, certbot, htpasswd, vhost_renderer, network)
├── templates/          # Jinja2 NGINX vhost templates
└── models/             # Re-exported Pydantic models
```

Key design decisions:
- **Jinja2 templates** for vhost generation (replaces sed placeholder substitution)
- **bcrypt** for htpasswd entries (replaces APR1/MD5)
- **`nginx -t` before reload** (validates config before applying)
- **Structured audit logging** to `/var/log/vsa/audit.jsonl` + `/var/lib/vsa/audit.db`
- **Pydantic config** with `VSA_ROOT` env var (no hardcoded paths)

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

NGINX vhost files live in `stacks/reverse-proxy/nginx/conf.d/<domain>.conf`. Every vhost includes `security_headers.conf` (HSTS, X-Frame-Options DENY, CSP) and rate limiting. Certbot handles Let's Encrypt via HTTP-01 (webroot). The `vsa site provision` command handles the full workflow: network attach, HTTP vhost, cert issuance, HTTPS vhost, nginx reload.

### Multi-VPS Strategy (Hub-and-Agent)

VPS-01 hosts the dashboard (hub). Additional VPS nodes run the CLI with `vsa agent` subcommand:
- `vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token XXX`
- `vsa agent start` (via systemd timer, every 30s)
- Sends heartbeats, container snapshots, cert status, and audit events to hub

## Conventions

- **Language defaults:** Python 3.11+, Node 20 LTS
- **Python tooling:** `uv` (preferred), Ruff for lint/format, pytest, FastAPI + Uvicorn, Typer for CLIs, Pydantic for config
- **Node tooling:** pnpm, ESLint, Prettier, Next.js + Tailwind
- **Docker:** Multi-stage builds, non-root users, slim base images, `HEALTHCHECK` required, images < 300MB, `restart: unless-stopped`
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

## Keeping Rules in Sync

After any significant change (new stacks, architectural shifts, new tooling, changed conventions), update **both**:
- **`CLAUDE.md`** (this file) — for Claude Code
- **`.cursor/rules/my.custom.rules.mdc`** — for Cursor IDE

This ensures all AI assistants stay aligned with the current state of the project.

## Implementation Status

All 7 phases of the VSA modernization plan have been implemented and committed.

### Completed
- **Phase 1 — Foundation:** Shared library (`packages/python/vsa-common/`), CLI skeleton, Jinja2 vhost renderer, bcrypt htpasswd service, 30 unit tests
- **Phase 2 — CLI Core:** All command modules (site, cert, auth, stack, vhost, bootstrap, agent) with audit logging
- **Phase 3 — Observability:** Loki 90-day retention, Promtail audit scrape pipeline, NGINX rate limiting zones
- **Phase 4 — Dashboard Backend:** FastAPI + SQLAlchemy async + PostgreSQL, 7 API routers, Alembic migrations, Docker SDK integration, multi-stage Dockerfile
- **Phase 5 — Dashboard Frontend:** Next.js 14 + Tailwind + React Query, 6 pages (overview, containers, domains, certs, audit, VPS), sidebar nav, status badges
- **Phase 6 — Multi-VPS Agent:** systemd service + timer units, agent register/start/status commands
- **Phase 7 — Cleanup:** Deprecated one-off scripts, updated Makefile to delegate to CLI, 4 ADRs, updated runbooks, consolidated root README

### Pending / Known Issues
- **GitHub Actions CI** (`.github/workflows/ci.yml`) — file exists locally but was removed from git because the GitHub PAT lacks the `workflow` scope. To re-add: update PAT with `workflow` scope, then `git add .github/workflows/ci.yml && git commit -m "ci: add GitHub Actions pipeline" && git push`
- **Dashboard not yet deployed** — `stacks/dashboard/` compose stack is ready but not yet running on VPS-01. Needs: create `/srv/flowbiz/dashboard/env/.env` from `.env.example`, `docker compose up -d --build`, `vsa site provision --domain dashboard.flowbiz.ai --container dashboard-ui --port 3000`
- **CLI not yet installed on VPS** — needs `cd apps/vps-admin-cli && uv tool install .` on the target VPS
- **Frontend dependencies not installed** — needs `cd apps/vps-admin-ui && pnpm install` before build
- **No `.env` files committed** — by design; `.env.example` files provide templates
- **`portfolio-api-commands.md`** — untracked personal notes file in repo root, not committed

### Test Status
- 30 CLI unit tests passing: `cd apps/vps-admin-cli && uv run pytest -q`
- Tests cover: Pydantic models, VsaConfig, bcrypt htpasswd, Jinja2 vhost rendering, audit logging (JSONL + SQLite)
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
