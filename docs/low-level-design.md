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
```

**Scrape targets (15s interval):**

| Job | Target | Port |
|-----|--------|------|
| `prometheus` | `prometheus:9090` | 9090 |
| `loki` | `loki:3100` | 3100 |
| `grafana` | `grafana:3000` | 3000 |
| `promtail` | `promtail:9080` | 9080 |
| `node-exporter` | `node-exporter:9100` | 9100 |
| `cadvisor` | `cadvisor:8080` | 8080 |

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

**Provisioning:** Bind-mounted from `/srv/flowbiz/observability/data/grafana-provisioning/` (datasources and dashboards configured as YAML).

**Data volume:** `obs-grafana-data` stores dashboards, users, alert state.

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

**Tables (6, managed by Alembic):**

| Table | Purpose | Growth |
|-------|---------|--------|
| `vps_nodes` | Registered VPS instances | Static (one row per VPS) |
| `domains` | Provisioned domains | Slow (one row per domain) |
| `certificates` | Cert status from remote agents | Slow |
| `audit_logs` | Audit trail from remote agents | Moderate |
| `container_snapshots` | Periodic container state from agents | Replaced on each sync |
| `traffic_stats` | Aggregated traffic from agents | Moderate |

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
├── grafana                   (port 3010→3000)
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
