# VSA Low-Level Design

Detailed component internals, storage layout, retention policies, and configuration specifics. For high-level architecture, see [architecture.md](architecture.md).

## Disk Layout

```
/etc/fstab:
  LABEL=cloudimg-rootfs  /                ext4  discard,commit=30,errors=remount-ro  0 1
  LABEL=docker           /var/lib/docker  ext4  defaults                             0 2
  LABEL=BOOT             /boot            ext4  defaults                             0 2
  LABEL=UEFI             /boot/efi        vfat  umask=0077                           0 1
```

### Root Disk (`/`)

Small SSD — holds OS, configs, and small state files. Must stay lean.

| Path | Contents | Size guidance |
|------|----------|---------------|
| `/srv/flowbiz/reverse-proxy/nginx/conf.d/` | NGINX vhost configs | ~1KB per domain |
| `/srv/flowbiz/reverse-proxy/nginx/snippets/` | Shared NGINX snippets | Static, <10KB |
| `/srv/flowbiz/reverse-proxy/nginx/auth/` | htpasswd files (bcrypt) | ~100B per domain |
| `/srv/flowbiz/reverse-proxy/letsencrypt/` | Let's Encrypt certs + renewal configs | ~50KB per domain |
| `/srv/flowbiz/reverse-proxy/certbot-www/` | ACME challenge webroot | Transient |
| `/srv/flowbiz/reverse-proxy/logs/` | NGINX access/error logs, per-domain JSON | Grows, needs rotation |
| `/srv/flowbiz/dashboard/data/postgres/` | PostgreSQL data directory | Grows slowly |
| `/srv/flowbiz/observability/data/grafana-provisioning/` | Grafana datasource/dashboard YAML | Static, <100KB |
| `/var/log/vsa/` | Audit log JSONL | Grows, one line per operation |
| `/var/lib/vsa/` | Audit SQLite database | Grows slowly |

### Docker Disk (`/dev/sdb` mounted at `/var/lib/docker`)

Large dedicated disk (246G) — holds Docker images, container layers, and named volumes.

| Volume | Mount inside container | Retention | Max size |
|--------|----------------------|-----------|----------|
| `obs-prometheus-data` | `/prometheus` | 15 days | 1GB (hard cap) |
| `obs-loki-data` | `/loki` | 30 days | ~500MB typical |
| `obs-grafana-data` | `/var/lib/grafana` | Indefinite | ~50MB typical |
| `obs-promtail-data` | `/var/lib/promtail` | N/A (position file) | <1MB |

The **BuildKit build cache** also lives here (`/var/lib/docker/buildkit`). It is
**not** touched by `docker system prune` — use `docker builder prune -af`. Left
unbounded it grows to multiple GB and (with default provenance attestations on)
can wedge `dockerd` — see the daemon note below.

### Docker Daemon (`/etc/docker/daemon.json`)

- **`live-restore: true`** (set 2026-06-19) — containers keep running when
  `dockerd` is restarted/upgraded/crashes (they are managed by `containerd-shim`
  + kernel iptables, not by `dockerd`). This makes a `systemctl restart docker`
  a **zero-downtime** operation: enable it, `systemctl reload docker` (SIGHUP,
  no stop), **verify `docker info --format '{{.LiveRestoreEnabled}}'` = `true`
  BEFORE restarting**, then restart. The only blip is the Docker API/CLI for a
  few seconds; running containers never stop.
- **`BUILDX_NO_DEFAULT_ATTESTATIONS=1`** (in `/etc/environment`, not daemon.json)
  — disables default BuildKit provenance attestations. Without it,
  `recordBuildHistory`/`ProvenanceCreator` goroutines accumulate per
  `compose up --build` and can recursively peg `dockerd` over the build-cache
  graph (the 2026-06-19 incident: 123 wedged goroutines = ~1.8 cores for 129
  days). Diagnose dockerd-internal CPU with `kill -USR1 <dockerd-pid>` →
  `/var/run/docker/goroutine-stacks-*.log`.

## Observability Stack Internals

### Prometheus (`prom/prometheus:v2.53.0`)

**Runs as:** image default user (`nobody`, UID 65534)

**Command flags:**
```
--config.file=/etc/prometheus/prometheus.yml
--storage.tsdb.path=/prometheus
--storage.tsdb.retention.time=15d
--storage.tsdb.retention.size=1GB
--web.external-url=https://prometheus.flowbiz.ai
--web.route-prefix=/
--web.enable-remote-write-receiver   # accept remote_write from vps-02/03 agents
```

**Networks:** `observability_internal` **and** `flowbiz_ext` (the latter so the
reverse-proxy nginx can reach the remote-write receiver — mirrors Loki).

**Scrape targets (15s interval):** the hub's own services. `node-exporter` and
`cadvisor` jobs are stamped `vps_id=vps-01` (static label) so they line up with
the remote-write series from vps-02/03.

| Job | Target | Port |
|-----|--------|------|
| `prometheus` | `prometheus:9090` | 9090 |
| `loki` | `loki:3100` | 3100 |
| `grafana` | `grafana:3000` | 3000 |
| `promtail` | `promtail:9080` | 9080 |
| `node-exporter` | `node-exporter:9100` | 9100 |
| `cadvisor` | `cadvisor:8080` | 8080 |
| `blackbox` | `blackbox-exporter:9115` `/probe` (relabel) | 9115 |

**Remote-write ingest (fleet-wide metrics):** vps-02/03 run the
`observability-agent` stack (node-exporter + cAdvisor + `prometheus-agent` in
`--enable-feature=agent` mode). Each agent scrapes its local exporters, stamps
`vps_id` via `external_labels` (`--enable-feature=expand-external-labels`), and
remote-writes to `https://loki.flowbiz.ai/prom/api/v1/write`. That vhost has a
`location /prom/` (`rewrite ^/prom/(.*)$ /$1`) proxying to
`observability-prometheus-1:9090`, reusing its TLS cert + IP allow-list + basic
auth (user `promtail`). The agent reads the password from a mounted file
(`secrets/remote_write_password`, host-only, gitignored) since Prometheus can't
expand env vars outside `external_labels`.

> **Footgun:** node-exporter runs in a bridge netns, so its `node_network_*`
> metrics only see the container's `eth0`, never the host `ens3`. Network panels
> use cAdvisor `container_network_*` instead.

**cAdvisor is CPU-tuned** (2026-06-19). Defaults collect dozens of metric groups
every 1s on every cgroup (~6–7% CPU). The compose `command` restricts it to the
4 families the Fleet Overview dashboard uses — `cpu`, `memory`, `network`,
`last_seen` — via `--disable_metrics=disk,diskIO,tcp,udp,advtcp,sched,process,hugetlb,referenced_memory,cpu_topology,resctrl,percpu,memory_numa,perf_event`,
plus `--docker_only=true` and `--housekeeping_interval=30s` (→ ~0.15% CPU). If a
dashboard panel needs a new metric family, re-enable it here. cAdvisor reads
cgroups from `/sys` + `/var/lib/docker` directly — it does **not** use the Docker
socket.

### Blackbox Exporter (`prom/blackbox-exporter:v0.25.0`)

External synthetic probing of public HTTPS endpoints — added 2026-07 to give the
**LokalFlash K8s app** (which runs off-fleet, on Infomaniak Kubernetes) a true
external uptime + TLS-expiry check. Config is `stacks/observability/blackbox.yml`
(one `http_2xx` module: `GET`, `follow_redirects: true`, `fail_if_not_ssl: true`,
`insecure_skip_verify: false` so an invalid cert is itself a probe failure). It
sits on `observability_internal` and reaches the public internet via the bridge
NAT; only Prometheus needs to reach it.

**Scrape mechanics** (`prometheus.yml`, job `blackbox`): the standard blackbox
relabel dance — `metrics_path: /probe`, `params.module: [http_2xx]`, and
`relabel_configs` copy the target URL into `__param_target`, set `instance` to
it, then rewrite `__address__` to `blackbox-exporter:9115`. Targets are stamped
`vps_id=ext` (synthetic, not a fleet host). Current targets:
`https://app.lokalflash.ch/api/health` and `https://www.lokalflash.ch/` — both
Cloudflare **DNS-only**, so the probe hits the K8s ingress directly (a real
origin test). The apex (`lokalflash.ch`) is CF-proxied and deliberately left out
to avoid alerting on Cloudflare-edge blips.

**Key series:** `probe_success` (1/0) and `probe_ssl_earliest_cert_expiry` (unix
ts of the earliest cert in the chain).

**Alerting** (`vsa alert`, `problems_from_blackbox` in `services/alerting.py`):
* **endpoint down** — `max_over_time(probe_success{job="blackbox"}[3m]) == 0`
  → critical. The `max_over_time` window means *every* scrape in 3 min failed,
  debouncing a single flaky probe.
* **cert expiry** — `(probe_ssl_earliest_cert_expiry - time())/86400` < 14d warn,
  < 3d critical. In healthy operation cert-manager renews ~30 d out, so this
  never fires unless renewal actually broke — it's a pure backstop.

Both flow through the existing state-diffed email pipe (only on change).
Thresholds are env-overridable: `VSA_ALERT_CERT_WARN_DAYS` / `_CRIT_DAYS`.

### K8s Backup Alerting (`problems_from_k8s_backups`)

The same alerter checks LokalFlash **backup freshness** off-cluster by reading
the prod K8s API read-only (SA `vsa-backup-monitor`, token+CA in
`/etc/vsa/k8s-backup-monitor.{token,ca.crt}`, root:600). Criticals: CNPG
`status.lastSuccessfulBackup` older than `VSA_ALERT_DB_BACKUP_MAX_HOURS` (26h);
condition `ContinuousArchiving=False` (WAL/PITR broken); `LastBackupSucceeded=False`;
and the `config-backup` CronJob `status.lastSuccessfulTime` older than
`VSA_ALERT_CONFIG_BACKUP_MAX_HOURS` (missing CronJob = warning). Config via
`VSA_ALERT_K8S_*`. API unreachable = no alarm (a cluster outage is already caught
by the blackbox probe). K8s-API not S3: no object-store creds off-cluster;
`lastSuccessfulBackup` is set only after the barman S3 upload, a faithful proxy.

> **Deploy gotcha:** the `vsa` CLI is a **uv tool** install (an isolated,
> non-editable copy at `~/.local/share/uv/tools/vsa-cli/`), so a `git pull` does
> **not** update the running alert code. After changing `alerting.py`, reinstall:
> `uv tool install --force ~/dev/github/VSA/apps/vps-admin-cli`. Compose/Prometheus
> config changes only need `docker compose up -d blackbox-exporter` +
> `docker compose restart prometheus` (re-reads the mounted `prometheus.yml`).

### Loki (`grafana/loki:3.0.0`)

**Runs as:** image default user (`loki`, UID 10001)

**Storage:** Filesystem-backed with BoltDB shipper (schema v12, 24h index period)

**Retention:**
- `retention_period: 30d`
- Compaction interval: 10 minutes
- Retention delete delay: 2 hours
- Worker count: 150

**Data layout inside volume:**
```
/loki/
├── chunks/       # Log chunk data
├── rules/        # Alerting rules
├── compactor/    # Compaction working directory
└── boltdb-shipper-*  # Index files
```

### Promtail (`grafana/promtail:3.0.0`)

**Scrape jobs:**

| Job | Source path | Labels extracted |
|-----|------------|-----------------|
| `system-journal` | `/var/log/journal` | `systemd_unit` |
| `nginx-access` | `/srv/flowbiz/*/logs/nginx/access*.log` | `remote_addr`, `method`, `status` |
| `nginx-domain-access` | `/srv/flowbiz/reverse-proxy/logs/domains/*.access.json` | `domain`, `method`, `status` |
| `nginx-error` | `/srv/flowbiz/*/logs/nginx/error*.log` | (none) |
| `vsa-audit` | `/var/log/vsa/audit.jsonl` | `actor`, `action`, `result`, `vps_id` |
| `docker-containers` | Docker socket discovery | `container`, `compose_service`, `compose_project`, `stream` |

### Grafana (`grafana/grafana:10.4.0`)

**Runs as:** image default user (`grafana`, UID 472)

**Host port:** published on **3011** (the `.env`'s `GRAFANA_HTTP_PORT=3010` is
stale — 3010 was already bound; the live container maps `3011:3000`). Confirm
with `docker port observability-grafana-1`.

**Admin:** `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` in
`stacks/observability/.env` (gitignored). Currently user **`info@flowbiz.ai`**.

**Datasources & dashboards:** created via the Grafana API and stored in the
`obs-grafana-data` volume (the `grafana-provisioning` bind mount is empty — they
are **not** YAML-provisioned). Datasource UIDs: Loki `ffl21vk4eobuoe`,
Prometheus `vsa-prometheus`. The primary dashboard `vsa-fleet-overview` is
generated from `stacks/observability/grafana/build_fleet_dashboard.py` (single
source of truth, committed as `dashboards/fleet-overview.json`) and POSTed to
`/api/dashboards/db`. **Each panel carries a panel-level `datasource`** or
Grafana falls back to the default (Loki) and PromQL panels error to "No data".

**Data volume:** `obs-grafana-data` stores dashboards, datasources, users, alert state.

## Reverse Proxy Internals

### NGINX (`nginx:1.25-alpine`)

**Container name:** `reverse-proxy-nginx`

**Ports:** 80 (HTTP), 443 (HTTPS)

**Volume mounts:**
| Host path | Container path | Mode |
|-----------|---------------|------|
| `/srv/flowbiz/reverse-proxy/nginx/conf.d/` | `/etc/nginx/conf.d/` | ro |
| `/srv/flowbiz/reverse-proxy/nginx/snippets/` | `/etc/nginx/snippets/` | ro |
| `/srv/flowbiz/reverse-proxy/nginx/auth/` | `/etc/nginx/auth/` | ro |
| `/srv/flowbiz/reverse-proxy/letsencrypt/` | `/etc/letsencrypt/` | rw |
| `/srv/flowbiz/reverse-proxy/certbot-www/` | `/var/www/certbot/` | rw |
| `/srv/flowbiz/reverse-proxy/logs/` | `/var/log/nginx/` | rw |

**Log format:** `json_detailed` — structured JSON with domain, method, URI, status, bytes, request time, upstream info, user agent. Per-domain log files at `/var/log/nginx/domains/<domain>.access.json`.

**Security headers** (included in every vhost via snippets):
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy`
- Rate limiting zones

### Certbot (`certbot/certbot:v2.10.0`)

**Container name:** `reverse-proxy-certbot`

**Renewal loop:** `while :; do certbot renew --webroot -w /var/www/certbot --quiet; sleep 12h; done`

**Renewal configs:** `/srv/flowbiz/reverse-proxy/letsencrypt/renewal/<domain>.conf` (one per provisioned domain, created automatically by `vsa site provision`)

### NGINX Reloader (`docker:27-cli`)

**Container name:** `reverse-proxy-reloader`

**Reload loop:** `while :; do sleep 6h; docker exec reverse-proxy-nginx nginx -s reload 2>/dev/null; done`

Picks up renewed TLS certificates by reloading NGINX config. Uses Docker socket (ro) to exec into the NGINX container.

## Dashboard Stack Internals

### PostgreSQL 16

**Data:** Bind-mounted at `/srv/flowbiz/dashboard/data/postgres/`

**Tables (8, managed by Alembic):**

| Table | Purpose | Growth |
|-------|---------|--------|
| `vps_nodes` | Registered VPS instances | Static (one row per VPS) |
| `domains` | Observed vhosts per VPS (agent-synced) | Slow |
| `certificates` | Observed certs per VPS, `sans` JSON column for SAN-aware drift; cascade-deleted with the node on `vsa vps remove` (fixed 2026-07-20 - previously orphaned) | Slow |
| `audit_logs` | Audit trail from remote agents | Moderate |
| `container_snapshots` | Periodic container state from agents | Replaced on each sync |
| `traffic_stats` | Aggregated traffic from agents | Moderate |
| `domain_assignments` | **Intent**: primary + warm-standby VPS list per domain (migration 0005) | Static (one row per domain) |
| `agent_commands` | Hub→agent command queue: `pending → running → completed`, JSON `argv`, captured `stdout`/`stderr` (migration 0006) | Bounded (TTL/reaper recommended in v2) |

**Migrations active on prod (vps-01):** `0001` initial → `0007` `certificates.sans`.
A subtle gotcha: `Base.metadata.create_all` at API boot creates *new tables*
but does **not ALTER existing ones**. After adding a column to an existing
table (e.g. `certificates.sans` in `0007`), do not just `alembic stamp` —
run `alembic upgrade head` to actually execute the `ALTER`.

### Schema details for the Phase A→E additions

**`domain_assignments`**

```
id PK
domain VARCHAR(255) UNIQUE NOT NULL  -- one row per domain (apex or sub)
primary_vps_id VARCHAR(64) NOT NULL  -- vps_id of the active host
standby_vps_ids JSON NOT NULL DEFAULT '[]'  -- list of warm-standby vps_ids
notes TEXT NOT NULL DEFAULT ''
created_at / updated_at TIMESTAMPTZ
```

Validation in the PUT endpoint: `primary_vps_id` may not also appear in
`standby_vps_ids`; `standby_vps_ids` is deduplicated and sorted before
storage.

**`agent_commands`**

```
id PK
vps_id VARCHAR(64) NOT NULL  -- target host (indexed)
argv JSON NOT NULL           -- e.g. ["cert", "health"]; agent prepends "vsa"
status VARCHAR(32) NOT NULL DEFAULT 'pending'  -- pending|running|completed
timeout_seconds INT NOT NULL DEFAULT 120
requested_by VARCHAR(128)    -- usually the unix user that ran `vsa fleet exec`
created_at / taken_at / completed_at TIMESTAMPTZ
exit_code INT NULL
stdout / stderr TEXT NULL    -- 64 KB cap, agent truncates with "...[truncated]"
```

Lifecycle: `POST /agent/exec` inserts with `status=pending`. Target VPS's
agent calls `POST /agent/commands/{id}/take` (atomic-ish — returns 409 if
already `running`/`completed`), runs the subprocess, then
`POST /agent/commands/{id}/result` to flip to `completed`. A reaper to
mark long-`running` rows as `timeout` is left for a future v2.

**`certificates.sans`** — JSON list, populated by agent from
`openssl x509 -ext subjectAltName -in <cert>`, with the cert's primary CN
prepended at index 0. Used by `/api/fleet/drift` to recognize that an apex
cert with `www.apex` in its SAN serves both names from one row.

### Dashboard API (FastAPI)

**Read-only mounts:**
- `/var/run/docker.sock` — container introspection via Docker SDK
- `/var/log/vsa` — audit JSONL (read by Promtail too)
- `/var/lib/vsa` — audit SQLite (direct reads for hub-local events)
- `/srv/flowbiz/reverse-proxy/letsencrypt` → `/etc/letsencrypt` — TLS cert parsing
- `/srv/flowbiz/reverse-proxy/nginx/conf.d` → `/etc/nginx/conf.d` — vhost file reading

## Network Topology

```
flowbiz_ext (shared bridge network)
├── reverse-proxy-nginx       (ports 80, 443)
├── reverse-proxy-certbot
├── loki                      (port 3100)
├── grafana                   (host port 3011→3000)
├── prometheus                (port 9090 — joined so nginx can proxy /prom/ remote-write)
├── dashboard-api
├── dashboard-ui
├── dify-*
└── any provisioned container

observability_internal (isolated bridge)
├── loki
├── grafana
├── promtail
├── prometheus                (port 9090)
├── node-exporter             (port 9100)
└── cadvisor                  (port 8080)

# Remote VPS (vps-02/03) — observability-agent stack
metrics-net (isolated bridge, per remote)
├── node-exporter
├── cadvisor
└── prometheus-agent          (remote-writes to the hub via loki.flowbiz.ai/prom/)

dashboard-net (isolated bridge)
├── dashboard-api
├── dashboard-ui
└── dashboard-postgres        (port 5432, NOT exposed publicly)

dify-net (isolated bridge)
├── dify-api
├── dify-web
├── dify-worker
├── dify-sandbox
├── dify-postgres
└── dify-redis
```

**Rule:** Database containers only join their stack's internal network. Never exposed on `flowbiz_ext`.

## Container User Model

| Container | Default user | UID | Notes |
|-----------|-------------|-----|-------|
| Prometheus | `nobody` | 65534 | Named volume initialized with image permissions |
| Loki | `loki` | 10001 | Named volume initialized with image permissions |
| Grafana | `grafana` | 472 | Named volume initialized with image permissions |
| Promtail | `root` | 0 | Needs access to host log files and Docker socket |
| Node Exporter | `nobody` | 65534 | Read-only host filesystem access |
| cAdvisor | `root` | 0 | Privileged, reads host metrics |
| NGINX | `nginx` | 101 | Writes to log volume |
| Certbot | `root` | 0 | Writes to letsencrypt volume |
| Dashboard API | non-root | — | Configured in Dockerfile |
| Dashboard UI | non-root | — | Configured in Dockerfile |
| PostgreSQL | `postgres` | 999 | Bind-mounted data directory |

## Configuration File Locations

| Config | Path | Format |
|--------|------|--------|
| Prometheus scrape config | `stacks/observability/prometheus.yml` | YAML |
| Loki storage + retention | `stacks/observability/loki-config.yml` | YAML |
| Promtail scrape jobs | `stacks/observability/promtail-config.yml` | YAML |
| NGINX vhost templates | `apps/vps-admin-cli/src/vsa/templates/` | Jinja2 |
| VSA CLI config | `apps/vps-admin-cli/src/vsa/config.py` | Python (Pydantic) |
| Dashboard API config | `apps/vps-admin-api/src/vsa_api/config.py` | Python (Pydantic) |
| DB migrations | `apps/vps-admin-api/alembic/versions/` | Python (Alembic) |
| System mounts | `/etc/fstab` | fstab |
| Docker daemon (`live-restore`) | `/etc/docker/daemon.json` | JSON (host-only) |
| BuildKit attestations toggle | `/etc/environment` (`BUILDX_NO_DEFAULT_ATTESTATIONS=1`) | env (host-only) |
| Hub/agent env (HUB_URL, HUB_AUTH, VPS_ID) | `/etc/vsa/agent.env` | env (mode 600) |
| Alerting config (SMTP, recipients, level, disk thresholds) | `/etc/vsa/alert.env` | env (mode 600, gitignored) |
| Remote-write password (per remote VPS) | `stacks/observability-agent/secrets/remote_write_password` | text (mode 644, gitignored) |
| Alert dedup state | `/var/lib/vsa/alert-state.json` | JSON (root-owned, written by timer) |
| DNS-01 Cloudflare token | `/srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini` | INI (mode 600) |
| reverse-proxy compose override toggle | `stacks/reverse-proxy/.env` (`COMPOSE_FILE=`) | env (gitignored) |

## Systemd Timers (hub, vps-01)

| Timer | Cadence | Runs | Env |
|-------|---------|------|-----|
| `vsa-agent.timer` | every 30s | `vsa agent start` | `/etc/vsa/agent.env` |
| `vsa-drift.timer` | daily 08:00 | `vsa fleet drift` | `/etc/vsa/agent.env` |
| `vsa-fleet-cert-health.timer` | weekly Mon 09:00 | `vsa fleet cert-health --all` | `/etc/vsa/agent.env` |
| `vsa-alert.timer` | every 15 min | `vsa alert check` | `/etc/vsa/agent.env` + `/etc/vsa/alert.env` |

(`vsa-agent.timer` also runs on vps-02/03; the rest are hub-only.)

## Alerting Internals (`vsa alert`)

- **Service:** `services/alerting.py` — `AlertConfig.from_env()` reads all
  `VSA_ALERT_*`; `collect_problems()` queries `/fleet/drift` + `/vps` +
  `/containers` + the prod K8s API (backup freshness); problems carry `(level, category, vps, target, detail)` and a
  stable `key` (`category|vps|target|level`).
- **Severity:** container `(unhealthy)` → warning; container not `Up` and not
  `Exited (0)` → critical; agent `last_seen` older than
  `VSA_ALERT_AGENT_STALE_MINUTES` → critical. Drift findings keep their reported
  level. Only `≥ VSA_ALERT_MIN_LEVEL` is kept.
- **Dedup state:** `/var/lib/vsa/alert-state.json` holds the sorted set of
  active problem `key`s. `vsa alert check` emails only when that set changes
  (new key → 🔴, empty after non-empty → ✅, no change → silent). `--force`
  overrides; `--dry-run` skips both send and state write.
- **Exit code:** `vsa alert check` exits 1 when a critical problem is active;
  the systemd unit sets `SuccessExitStatus=0 1` so the timer never trips an
  OnFailure loop.
- **SMTP:** stdlib `smtplib` over STARTTLS (port 587), multipart text+HTML. No
  new Python dependency.
