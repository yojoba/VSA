# VSA Architecture

## Overview

FlowBiz VPS Admin Suite (VSA) is a monorepo for managing multi-tenant hosting infrastructure. It orchestrates Docker Compose stacks for AI apps, customer websites, and internal tooling behind an NGINX reverse proxy with automated TLS.

```
                    ┌──────────────────────────────────────────┐
                    │              Internet                     │
                    └──────────────────┬───────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │         NGINX Reverse Proxy               │
                    │    (TLS termination, rate limiting,       │
                    │     security headers, per-domain logs)    │
                    └──┬────┬────┬────┬────┬────┬────┬────┬───┘
                       │    │    │    │    │    │    │    │
              ┌────────┘    │    │    │    │    │    │    └────────┐
              ▼             ▼    ▼    ▼    ▼    ▼    ▼             ▼
         Dashboard      Dify  n8n  CFO  Grafana  ...        Customer
         (API + UI)                                          Sites
```

## System Components

### 1. VSA CLI (`apps/vps-admin-cli/`)

Python/Typer CLI that replaces all legacy bash scripts. Primary tool for infrastructure operations.

```
vsa/
├── cli.py              # Root Typer app, command group registration
├── config.py           # VsaConfig singleton (VSA_ROOT env var)
├── audit.py            # Dual-write JSONL + SQLite audit logger
├── errors.py           # Custom exceptions
├── commands/
│   ├── site.py         # provision/unprovision domains
│   ├── cert.py         # SSL certificate management
│   ├── auth.py         # HTTP Basic Auth (bcrypt)
│   ├── stack.py        # Docker Compose stack lifecycle
│   ├── vhost.py        # NGINX vhost synchronization
│   ├── bootstrap.py    # Initial VPS setup
│   └── agent.py        # Multi-VPS agent (register/start/status)
├── services/
│   ├── docker.py       # Docker CLI/SDK wrapper
│   ├── nginx.py        # nginx -t validation and reload
│   ├── certbot.py      # Let's Encrypt operations
│   ├── htpasswd.py     # bcrypt password file management
│   ├── vhost_renderer.py  # Jinja2 NGINX vhost templates
│   ├── network.py      # Docker network operations
│   └── agent_sync.py   # Traffic stats collection and hub sync
├── templates/          # Jinja2 NGINX vhost templates
└── models/             # Re-exported Pydantic models from vsa-common
```

### 2. Dashboard API (`apps/vps-admin-api/`)

FastAPI async backend providing REST endpoints for the management dashboard.

**Stack:** FastAPI + SQLAlchemy (async) + PostgreSQL + Loki

**Routers (9):**

| Router | Endpoint | Data Source |
|--------|----------|-------------|
| `containers` | `GET /api/containers` | Docker SDK (live) |
| `domains` | `GET /api/domains` | PostgreSQL |
| `certs` | `GET /api/certs` | Disk (Let's Encrypt cert files) |
| `traffic` | `GET /api/traffic/stats` | Loki (LogQL metric queries) |
| `traffic` | `GET /api/traffic/logs` | Loki (raw log entries) |
| `audit_logs` | `GET /api/audit-logs` | PostgreSQL |
| `stacks` | `GET /api/stacks` | Docker SDK (live) |
| `vps` | `GET /api/vps` | PostgreSQL |
| `agent` | `POST /api/agent/*` | Agent sync (ingest) |

**Services:**
- `loki.py` — Loki client for raw log queries and LogQL metric aggregation

**Database (PostgreSQL):**

6 tables managed by Alembic:
- `vps_nodes` — registered VPS instances
- `domains` — provisioned domains with container/port mapping
- `certificates` — (legacy, kept for agent sync from remote VPS)
- `audit_logs` — infrastructure operation audit trail
- `container_snapshots` — periodic container state from agents
- `traffic_stats` — aggregated traffic stats from agents

### 3. Dashboard UI (`apps/vps-admin-ui/`)

Next.js 14 frontend with Tailwind CSS and React Query.

**Pages (7):**

| Route | Purpose |
|-------|---------|
| `/` | Overview — system summary |
| `/containers` | Docker container status |
| `/domains` | Provisioned domains list |
| `/certificates` | SSL cert expiry with days remaining and status badges |
| `/traffic` | Traffic analytics — stats cards, per-domain table, raw logs |
| `/audit` | Audit log viewer with pagination |
| `/vps` | VPS node information |

### 4. Shared Library (`packages/python/vsa-common/`)

Pydantic models and constants shared between CLI and API:
- `AuditEvent` — structured audit log entry
- `SiteConfig` — domain provisioning config
- `VsaConfig` — path configuration

### 5. Docker Compose Stacks (`stacks/`)

| Stack | Services | Purpose |
|-------|----------|---------|
| `reverse-proxy` | NGINX 1.25, Certbot, NGINX Reloader | TLS termination, routing, per-domain JSON logging, auto-renewal |
| `dashboard` | PostgreSQL 16, FastAPI, Next.js | Management dashboard |
| `observability` | Grafana 10.4, Loki 3.0, Promtail 3.0, Prometheus 2.53, Node Exporter, cAdvisor | Monitoring and log aggregation |
| `dify` | Dify platform | AI/LLM workbench |
| `llm-gateway` | (placeholder) | LLM backend routing |

## Data Flows

### Traffic Analytics Pipeline

```
NGINX                     Promtail                  Loki                    API                     UI
  │                          │                        │                      │                       │
  │ json_detailed log        │                        │                      │                       │
  │ per domain               │                        │                      │                       │
  ├──────────────────────────▶                        │                      │                       │
  │ /var/log/nginx/domains/  │ scrape job:            │                      │                       │
  │   *.access.json          │ nginx-domain-access    │                      │                       │
  │                          │ extract labels:        │                      │                       │
  │                          │ domain, method, status │                      │                       │
  │                          ├────────────────────────▶                      │                       │
  │                          │                        │ store with labels     │                       │
  │                          │                        │                      │                       │
  │                          │                        │◀─────────────────────┤ LogQL metric queries  │
  │                          │                        │ count_over_time      │ GET /api/traffic/stats │
  │                          │                        │ sum_over_time        │                       │
  │                          │                        │ avg_over_time        │                       │
  │                          │                        ├─────────────────────▶│                       │
  │                          │                        │ aggregated results   │                       │
  │                          │                        │                      ├──────────────────────▶│
  │                          │                        │                      │ stats + raw logs      │
```

**NGINX log format** (`json_detailed`): Structured JSON with domain, method, URI, status, bytes, request time, upstream info, user agent.

**Promtail labels:** `domain`, `method`, `status` — enables efficient LogQL filtering.

**LogQL queries used for stats:**
- `sum by (domain) (count_over_time({job="nginx-domain-access"}[PERIOD]))` — request counts
- Same with `status=~"2.."`, `"3.."`, `"4.."`, `"5.."` — status breakdown
- `sum by (domain) (sum_over_time(... | json | unwrap body_bytes_sent [...]))` — bandwidth
- `avg by (domain) (avg_over_time(... | json | unwrap request_time [...]))` — response time

### Certificate Monitoring

```
Let's Encrypt cert files on disk
  /etc/letsencrypt/live/<domain>/fullchain.pem
    │
    ▼
  Dashboard API (cryptography library)
    → parse x509 → extract not_valid_after_utc
    → compute days_remaining
    → classify status: valid (>30d), warning (<=30d), critical (<=14d), expired
    │
    ▼
  Dashboard UI
    → color-coded table with expiry, days left, status badges
```

### Audit Logging

```
CLI operation
  │
  ├──▶ /var/log/vsa/audit.jsonl  ──▶  Promtail  ──▶  Loki  ──▶  Grafana
  │
  └──▶ /var/lib/vsa/audit.db     ──▶  Agent sync  ──▶  Dashboard API  ──▶  Dashboard UI
```

### Multi-VPS Hub-and-Agent

```
VPS-01 (Hub)                              VPS-02..N (Agents)
┌─────────────────────┐                   ┌─────────────────────┐
│ Dashboard API       │◀──────────────────│ vsa agent start     │
│   POST /agent/      │   heartbeat       │   (systemd timer    │
│     heartbeat       │   containers      │    every 30s)       │
│     containers      │   certs           │                     │
│     domains         │   traffic-sync    │ Collects:           │
│     traffic-sync    │   audit-events    │   container state   │
│     audit-events    │                   │   cert status       │
│                     │                   │   traffic stats     │
│ PostgreSQL          │                   │   audit events      │
│   (stores all data) │                   │                     │
└─────────────────────┘                   └─────────────────────┘
```

## Networking

```
                   flowbiz_ext (shared Docker network)
    ┌──────────────────────────────────────────────────────┐
    │                                                      │
┌───┴───┐   ┌───────┐   ┌──────────┐   ┌──────────────┐   │
│ NGINX │   │ Dify  │   │Dashboard │   │ Observability │   │
│       │   │       │   │ API + UI │   │ Grafana/Loki  │   │
└───────┘   └───┬───┘   └────┬─────┘   └──────┬───────┘   │
                │            │                 │           │
            dify-net    dashboard-net    observability-net  │
                │            │                 │           │
            ┌───┴───┐   ┌───┴────┐      ┌────┴─────┐     │
            │Dify DB│   │Postgres│      │Prometheus│     │
            └───────┘   └────────┘      └──────────┘     │
    └──────────────────────────────────────────────────────┘
```

- **`flowbiz_ext`** — shared external network, all stacks join for reverse proxy access
- **`<stack>-net`** — internal bridge per stack for inter-service communication
- **No database ports exposed publicly**

## Data Path Convention

```
/srv/<tenant>/<app>/
├── data/    # Application data, DB volumes
├── env/     # .env files (chmod 640, never committed)
├── logs/    # Application logs
└── compose/ # Optional compose overrides
```

Example: `/srv/flowbiz/reverse-proxy/letsencrypt/` stores all TLS certificates.

## Reverse Proxy & TLS

- NGINX vhost files: `stacks/reverse-proxy/nginx/conf.d/<domain>.conf`
- Security headers: HSTS, X-Frame-Options DENY, CSP, rate limiting
- Log format: `json_detailed` → per-domain files at `/var/log/nginx/domains/<domain>.access.json`
- Log format config: `stacks/reverse-proxy/nginx/snippets/log_format_json.conf` (included via `00-log-format.conf`)
- TLS: Let's Encrypt via HTTP-01 (webroot), Certbot auto-renew every 12h
- Provisioning: `vsa site provision` handles full workflow (network attach, HTTP vhost, cert issuance, HTTPS vhost, nginx reload)
- Unprovisioning: `vsa site unprovision` performs 6-step cleanup with shared container detection

### Certificate Auto-Renewal Architecture

```
stacks/reverse-proxy/compose.yml
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  reverse-proxy-certbot (certbot/certbot:v2.10.0)                │
│    └─ loop: certbot renew → sleep 12h → repeat                  │
│    └─ mounts: /etc/letsencrypt, /var/www/certbot                │
│    └─ checks all certs in /etc/letsencrypt/renewal/*.conf        │
│    └─ auto-renews any cert within 30 days of expiry              │
│                                                                  │
│  reverse-proxy-reloader (docker:27-cli)                          │
│    └─ loop: sleep 6h → docker exec nginx -s reload → repeat     │
│    └─ mounts: /var/run/docker.sock (ro)                          │
│    └─ picks up renewed certs by reloading NGINX                  │
│                                                                  │
│  reverse-proxy-nginx (nginx:1.25-alpine)                         │
│    └─ serves traffic, mounts /etc/letsencrypt for cert access    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Site Unprovision Flow

```
vsa site unprovision --domain example.com
  │
  ├─ [1/6] Remove vhost config (repo + mount directories)
  ├─ [2/6] Remove auth/htpasswd files
  ├─ [3/6] Delete Let's Encrypt certificate (certbot delete)
  ├─ [4/6] Remove per-domain access log files
  ├─ [5/6] Handle container:
  │    ├─ If shared with other domains → warn user, ask to keep/remove
  │    ├─ If --keep-container → disconnect from network only
  │    └─ Otherwise → stop, remove, disconnect from network
  └─ [6/6] Reload NGINX
```

## Reboot Resilience

All services are configured to survive unattended reboots:

- **Docker daemon**: `systemctl enable docker` — starts on boot
- **All containers**: `restart: unless-stopped` or `restart: always` — Docker restarts them after boot
- **Compose stacks**: All compose files include `restart: unless-stopped` for every service
- **No systemd dependencies**: All services run as Docker containers, no external systemd units required for core functionality

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| CLI | Python, Typer, Jinja2, bcrypt | 3.11+ |
| API | FastAPI, SQLAlchemy (async), asyncpg, cryptography | 0.115+ |
| Frontend | Next.js, Tailwind CSS, React Query | 14 |
| Database | PostgreSQL | 16 |
| Reverse Proxy | NGINX | 1.25 |
| TLS | Certbot (Let's Encrypt) | 2.10 |
| Log Aggregation | Grafana Loki, Promtail | 3.0 |
| Monitoring | Grafana, Prometheus, Node Exporter, cAdvisor | 10.4 / 2.53 |
| Container Runtime | Docker, Docker Compose | v2 |
| Package Management | uv (Python), pnpm (Node) | latest |
| Linting | Ruff (Python), ESLint (JS) | latest |

## API Dependencies

Key Python packages for the Dashboard API:
- `fastapi` + `uvicorn` — async web framework
- `sqlalchemy[asyncio]` + `asyncpg` — async PostgreSQL ORM
- `alembic` — database migrations
- `httpx` — async HTTP client (Loki queries)
- `docker` — Docker SDK for container introspection
- `cryptography` — X.509 certificate parsing for live expiry data
- `pydantic` + `pydantic-settings` — config and validation

## Deployment

The dashboard runs on VPS-01 at `https://dashboard.flowbiz.ai/`:

```bash
# Rebuild and deploy API + UI
cd stacks/dashboard
docker compose up -d --build

# API-only rebuild (faster)
docker compose up -d --build dashboard-api

# UI-only rebuild
docker compose up -d --build dashboard-ui
```

The API container mounts:
- `/var/run/docker.sock` (ro) — container introspection
- `/var/log/vsa` (ro) — audit logs
- `/var/lib/vsa` (ro) — agent state
- `/srv/flowbiz/reverse-proxy/letsencrypt` → `/etc/letsencrypt` (ro) — TLS cert files
