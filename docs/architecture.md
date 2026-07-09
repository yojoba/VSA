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
│   ├── vps.py          # VPS fleet management (list/add/remove)
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
| `audit_logs` | `GET /api/audit-logs` | Local SQLite + PostgreSQL (merged) |
| `stacks` | `GET /api/stacks` | Docker SDK (live) |
| `vps` | `GET /api/vps` | PostgreSQL |
| `agent` | `POST /api/agent/*` | Agent sync (ingest) |
| `agent` | `DELETE /api/agent/vps/{id}` | Remove VPS and associated data |

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
| `observability` | Grafana 10.4, Loki 3.0, Promtail 3.0, Prometheus 2.53, Node Exporter, cAdvisor, Blackbox Exporter | Monitoring and log aggregation (named volumes on dedicated disk); hub-side. Prometheus has `--web.enable-remote-write-receiver` and joins `flowbiz_ext`. Blackbox Exporter adds external synthetic probes of public endpoints (see Data Flows) |
| `observability-agent` | Promtail, Node Exporter, cAdvisor, Prometheus (agent mode) | Remote-VPS agent (vps-02/03): ships **logs** to the hub Loki and **metrics** to the hub Prometheus via remote-write |
| `dify` | Dify platform | AI/LLM workbench |
| `llm-gateway` | (placeholder) | LLM backend routing |

The primary Grafana dashboard is **VSA — Fleet Overview** (`uid=vsa-fleet-overview`),
generated from `stacks/observability/grafana/build_fleet_dashboard.py` and
covering host metrics, container metrics, web traffic and logs/audit — all
filterable per VPS.

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
  └──▶ /var/lib/vsa/audit.db ──┬──▶  Dashboard API (direct read, hub-local)  ──▶  Dashboard UI
                               │
                               └──▶  Agent sync (remote VPS)  ──▶  Dashboard API (PostgreSQL)
```

**Hub-local events:** The API container mounts `/var/lib/vsa` (ro) and reads the local SQLite
audit DB directly — no agent sync dependency for hub events.

**Remote events:** Agent sync on remote VPS nodes sends events to `POST /api/agent/audit-sync`,
stored in PostgreSQL.

**Merge & dedup:** The `/api/audit-logs` endpoint merges both sources, deduplicates by
`(timestamp, actor, action, target)`, and returns paginated results sorted newest-first.

### Fleet-wide Metrics (Prometheus remote-write, push model)

Host + container metrics from **all** VPS land in the hub's Prometheus. The hub
scrapes its own node-exporter/cAdvisor (stamped `vps_id=vps-01`). Remote VPS push:

```
VPS-02..N (observability-agent)                 VPS-01 (Hub)
┌───────────────────────────────┐               ┌──────────────────────────────────┐
│ node-exporter + cAdvisor       │   remote_     │ NGINX  loki.flowbiz.ai  /prom/    │
│   ▲ scrape (local)             │   write       │   (TLS + IP allow-list + auth)    │
│ prometheus-agent (agent mode) ─┼──── HTTPS ───▶│   ▼  /prom/api/v1/write           │
│   external_labels: vps_id      │               │ Prometheus (remote-write receiver)│
└───────────────────────────────┘               │   ▼                               │
                                                 │ Grafana ◀── VSA Fleet Overview    │
                                                 └──────────────────────────────────┘
```

No new public endpoint, DNS record or cert is needed on the remotes — the
write path reuses the `loki.flowbiz.ai` vhost. `vsa alert`'s **disk** check
reads this same Prometheus, so disk pressure on any VPS raises an email alarm.

### External Synthetic Monitoring (blackbox, pull model)

Blackbox Exporter on the hub probes **public HTTPS endpoints from OFF the target
system** — a true external vantage. It was added to watch the **LokalFlash K8s
app** (`app.lokalflash.ch`, `www.lokalflash.ch`), which runs on a separate
Infomaniak Kubernetes cluster with its own in-cluster Sentry. Sentry only sees
errors when the app *runs*; a dead edge (ingress down, DNS, TLS-cert lapse)
produces no Sentry event at all. The hub, being outside that cluster, catches
exactly that blind spot.

```
VPS-01 (Hub, observability stack)                    Public edge (K8s cluster)
┌──────────────────────────────────────┐             ┌───────────────────────────┐
│ Prometheus ── scrape /probe ─────────▶│  probe      │ Cloudflare (DNS-only) ──▶ │
│   job "blackbox"                       │  (HTTPS) ──▶│   K8s ingress ──▶ api/www │
│      ▼ probe_success                   │◀────────────│                           │
│      ▼ probe_ssl_earliest_cert_expiry  │             └───────────────────────────┘
│ vsa alert (problems_from_blackbox) ──▶ email (alexandre@netcool.ch) on change
└──────────────────────────────────────┘
```

`app`/`www` are Cloudflare **DNS-only**, so the probe hits the K8s ingress
directly (a genuine origin test, not a Cloudflare-edge test). Two alarms flow
through the same `vsa alert` email pipe as the disk check: **endpoint down**
(`probe_success==0` sustained ≥3 min) and **TLS-cert expiry** (backstop for
cert-manager silently failing to renew). No agent or config lives on the K8s
side — this is entirely hub-side and pull-based.

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
│   DELETE /agent/    │                   │   traffic stats     │
│     vps/{id}        │                   │   audit events      │
│                     │                   │                     │
│ PostgreSQL          │                   │                     │
│   (stores all data) │                   │                     │
└─────────────────────┘                   └─────────────────────┘
```

#### VPS Fleet Management

```bash
vsa vps list                                    # List all VPS nodes (table)
vsa vps add --id vps-02 --hostname X --ip Y     # Pre-register a VPS
vsa vps remove vps-02 [-y]                      # Remove VPS + all data
```

**Adding a new VPS:**
1. On hub: `vsa vps add --id vps-02 --hostname newserver --ip 1.2.3.4`
2. On new VPS: `vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token <token>`
3. On new VPS: `vsa agent start` (systemd timer syncs every 30s)

#### Agent Sync Reconciliation

Sync endpoints perform **full reconciliation** — not append-only:

```
Agent payload (current state from vhost files)
  │
  ▼
API endpoint (domains-sync / certs-sync)
  │
  ├─ UPSERT: domains present in payload → insert or update in DB
  │
  └─ DELETE: domains in DB for this VPS that are NOT in payload → removed
     (handles cleanup after `vsa site unprovision`)
```

| Endpoint | Strategy | Stale Entry Handling |
|----------|----------|---------------------|
| `domains-sync` | Upsert + delete stale by `vps_id` | Domains removed after unprovision |
| `certs-sync` | Upsert + delete stale | Certs removed after cert deletion |
| `containers-sync` | Full replacement (delete all + re-insert) | Always fresh |
| `DELETE /agent/vps/{id}` | Cascade delete | Removes VPS + domains + snapshots + traffic |

## Write-Side Multi-VPS (Phase A→E, 2026-05-04)

The agent-sync table above is the **read** path. The **write** path was
added in a second pass: a hub-stored command queue + an intent registry,
both polled by the existing agents on their 30s tick.

```
┌─ on the hub ────────────────────────┐         ┌─ on each VPS ──────────────┐
│                                     │         │                            │
│  vsa fleet exec --vps X -- ...      │         │  vsa-agent.timer (30s)     │
│         │                           │         │         │                  │
│         ▼ POST /api/agent/exec      │         │         ▼                  │
│  ┌──────────────────────────────┐   │         │  ┌──────────────────────┐  │
│  │ agent_commands (queue)       │   │ ◀───────│  │ GET /agent/commands  │  │
│  │  pending → running →         │   │         │  │ ?vps_id=X            │  │
│  │  completed                   │   │ ───────▶│  │ POST .../take        │  │
│  │                              │   │         │  │ subprocess.run([     │  │
│  │ domain_assignments (intent)  │   │         │  │   "vsa", *argv])     │  │
│  └──────────────────────────────┘   │         │  │ POST .../result      │  │
│         ▲                           │         │  └──────────────────────┘  │
│         │ GET /api/fleet/drift      │         │                            │
│  /health page (every 60s)           │         │                            │
└─────────────────────────────────────┘         └────────────────────────────┘
```

### Intent vs observed state

| Table | Source of truth | Purpose |
| --- | --- | --- |
| `domains` (sync) | what each agent sees in `cfg.mount_vhost_dir` | observed reality |
| `certificates` (sync) | what each agent sees in `cfg.letsencrypt_live_dir` (with SAN extraction) | observed reality |
| `domain_assignments` | user, edited via `vsa fleet assign` / PUT `/api/assignments/{domain}` | declared intent |
| `agent_commands` | user, written by `vsa fleet exec` / orchestrators / `POST /api/agent/exec` | command queue |

`/api/fleet/drift` joins these in Python and reports any mismatch
(`missing-on-primary`, `rogue-host`, `cert-missing`, `cert-expiring-soon`,
`cert-expired`, etc.). SAN-aware: a cert covering `apex + www.apex` is
treated as serving both names on the same VPS.

### Hub→agent execution flow

1. Hub-side caller (CLI or orchestrator) POSTs `/api/agent/exec` with
   `{vps_id, argv, timeout_seconds}`. Returns a row id.
2. Caller polls `GET /api/agent/commands/{id}` every 2s.
3. Target agent on its next 30s sync tick calls
   `GET /agent/commands?vps_id=X&status=pending&limit=10`.
4. For each pending row, agent calls `POST /agent/commands/{id}/take`
   (returns 409 if someone else got it) → status flips to `running`.
5. Agent runs `subprocess.run(["vsa", *argv], timeout=timeout_seconds,
   capture_output=True, text=True)`.
6. Agent posts `POST /agent/commands/{id}/result` with
   `{exit_code, stdout, stderr}` (each capped at 64 KB) → status flips to
   `completed`.
7. Caller's next poll sees `completed`, prints output, exits with
   `exit_code`.

Latency is bounded by one agent tick (~30s). Agents never accept inbound
connections from the hub — they always initiate.

See [ADR-006](ADRs/006-hub-to-agent-execution.md) for the design rationale
(pull vs push, security envelope, alternatives considered).

### Warm-standby flow (`vsa fleet site-provision`)

```
hub (CLI)                          primary VPS                      standby VPS
   │
   │ enqueue "site provision -d X -c c -p p"
   ├──────────────────────────────────▶ vhost (HTTP-only ACME) ─▶ HTTP-01 webroot
   │                                    cert via Let's Encrypt
   │                                    HTTPS vhost + reload
   │ ◀──────────────────────────────── result OK
   │
   │ enqueue "site provision -d X -c c -p p --standby"
   ├──────────────────────────────────────────────────────────────▶ vhost (skip ACME stub)
   │                                                                cert via DNS-01 Cloudflare
   │                                                                HTTPS vhost + reload
   │                                                                (no container attach,
   │                                                                 no backend running)
   │ ◀────────────────────────────────────────────────────────────  result OK
   │
   ▼ PUT /api/assignments/X { primary, standbys, notes }
```

The standby ends up with a valid cert + working vhost ready to serve
traffic the moment a failover updates DNS — the missing piece is just
the application container itself, which is deployed separately.

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

## Storage Architecture

### Two-Disk Layout

```
/dev/sda (root /)                          /dev/sdb (/var/lib/docker)
├── /srv/flowbiz/                          ├── /var/lib/docker/volumes/
│   ├── reverse-proxy/                     │   ├── obs-prometheus-data/   (15d / 1GB cap)
│   │   ├── nginx/conf.d/   (vhosts)      │   ├── obs-loki-data/         (30d retention)
│   │   ├── letsencrypt/    (TLS certs)    │   ├── obs-grafana-data/
│   │   ├── logs/           (access logs)  │   └── obs-promtail-data/
│   │   └── auth/           (htpasswd)     │
│   ├── dashboard/data/     (PostgreSQL)   ├── /var/lib/docker/overlay2/  (container layers)
│   └── observability/data/                └── /var/lib/docker/containers/ (container logs)
│       └── grafana-provisioning/ (config)
├── /var/log/vsa/            (audit JSONL)
└── /var/lib/vsa/            (audit SQLite)
```

**Design principle:** Heavy, growing data (metrics, logs, container images) lives on the dedicated Docker disk (`/dev/sdb`, 246G). Configuration, certificates, and small state files stay on root.

### Data Path Convention

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
- Multipoint provisioning: `--route` option (repeatable) routes different URL paths to different containers
- Unprovisioning: `vsa site unprovision` performs 6-step cleanup with shared container detection

### Multipoint Provisioning

Use `--route` (repeatable) to route different URL paths to different containers behind a single domain:

```bash
# Example: promoflash.flowbiz.ai with frontend + PocketBase backend
vsa site provision --domain promoflash.flowbiz.ai \
  --route /=promoflash-frontend:80 \
  --route /api/=promoflash-pocketbase:8090 \
  --route /_/=promoflash-pocketbase:8090
```

Result:
- `/` → `promoflash-frontend:80` (frontend app)
- `/api/*` → `promoflash-pocketbase:8090` (PocketBase API)
- `/_/*` → `promoflash-pocketbase:8090` (PocketBase admin UI)

The CLI generates an NGINX vhost with multiple `location` blocks, each routing to the specified container. All containers are automatically connected to the `flowbiz_ext` network.

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

## Fleet Alerting (email alarms)

`vsa alert` turns observed fleet state into email alarms. It is **read-only
over the hub API** — no new data source — and runs on the hub via
`vsa-alert.timer` (every 15 min).

```
vsa-alert.timer (15 min)
  └─ vsa alert check
       ├─ GET /api/fleet/drift   → cert + layout problems
       ├─ GET /api/vps           → agents with stale last_seen (system down)
       ├─ GET /api/containers    → down / unhealthy containers
       │
       ├─ filter ≥ VSA_ALERT_MIN_LEVEL  →  list[Problem]
       ├─ diff vs /var/lib/vsa/alert-state.json
       │     new/escalated problem  → 🔴 email digest
       │     all cleared            → ✅ recovery email
       │     unchanged              → silent (no email)
       └─ SMTP STARTTLS (stdlib) → recipients
```

Design points:
- **Email only on change** — the state file makes alerts idempotent per
  problem-set, so a persistent issue isn't re-mailed every 15 min.
- **Transport-agnostic core** — `services/alerting.py` produces a `Problem`
  list + renders text/HTML; only `send_email` is SMTP-specific (extend there
  for Slack/Telegram).
- **Config is operational, not code** — all `VSA_ALERT_*` env in
  `/etc/vsa/alert.env` (gitignored). On/off = enable/disable the timer.
- See `docs/runbooks/alerting.md`.

## Data Retention Policies

| Data | Retention | Enforcement |
|------|-----------|-------------|
| Prometheus metrics | 15 days / 1GB max | `--storage.tsdb.retention.time=15d` + `--storage.tsdb.retention.size=1GB` |
| Loki logs | 30 days | `retention_period: 30d` in loki-config.yml, compactor deletes expired chunks |
| Audit logs (SQLite) | Indefinite | Local file on root disk |
| Audit logs (PostgreSQL) | Indefinite | Dashboard database |
| PostgreSQL (dashboard) | Indefinite | Bind mount on root disk |
| NGINX access logs | Indefinite (disk-managed) | Bind mount on root disk, per-domain JSON files |
| Container logs | Docker default (100MB/container) | Managed by Docker daemon |

## Reboot Resilience

All services are configured to survive unattended reboots:

- **Docker daemon**: `systemctl enable docker` — starts on boot
- **`live-restore`** (`/etc/docker/daemon.json`, hub): containers keep running
  when `dockerd` itself is restarted/upgraded/crashes — only the Docker API
  blips, not the workloads. Makes a `dockerd` restart a zero-downtime operation
  (verify `LiveRestoreEnabled=true` before restarting). See LLD § Docker Daemon.
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
