# Observability Stack

Centralised logging and metrics for FlowBiz VPS environments using Grafana, Loki, Promtail, Prometheus, Node Exporter and cAdvisor.

## Components

| Service        | Purpose                                           | Ports |
| -------------- | -------------------------------------------------- | ----- |
| Grafana        | Dashboards, alerting and visualisation             | `3010` (default — avoids conflict with handsome-app on 3001) |
| Loki           | Log aggregation backend (multitenant capable)      | `3100` |
| Promtail       | Log shipper (systemd, NGINX, Docker containers)    | - |
| Prometheus     | Metrics storage and alert evaluation               | `9090` |
| Node Exporter  | Host-level metrics (CPU, RAM, filesystem, etc.)    | `9100` |
| cAdvisor       | Container-level metrics (CPU, memory, network)     | `8080` |

All services share the internal bridge network `observability_internal`. Grafana also joins the external `flowbiz_ext` network so it can be proxied via NGINX.

> Default host ports are chosen to avoid clashes with existing stacks (e.g. Grafana runs on 3010 because port 3001 is already bound by handsome-app). Adjust `.env` if your environment differs.

## Data & Logs

Persistent data lives under `/srv/flowbiz/observability`:

```
/srv/flowbiz/observability/
├── data/
│   ├── grafana/
│   ├── grafana-provisioning/
│   ├── loki/
│   ├── promtail/
│   └── prometheus/
├── env/
│   └── .env
└── logs/ (optional scratch space)
```

> **Permissions:** create the directories as `root:docker` with `chmod 750` so Promtail can read NGINX logs but secrets remain protected.

## Configuration

1. Copy the example env file and adjust values (domains, admin credentials, ports):
   ```bash
   mkdir -p /srv/flowbiz/observability/{data,env,logs}
   cp stacks/observability/.env.example /srv/flowbiz/observability/env/.env
   ```
2. Review `promtail-config.yml` to ensure log paths align with your NGINX locations. By default it tails `/srv/flowbiz/*/logs/nginx/access*.log` and `error*.log` plus Docker container logs via the Docker socket.
3. Edit `prometheus.yml` to add any additional scrape jobs (application exporters).
4. Optional: adjust `loki-config.yml` retention or storage settings.

## Deployment

From the repo root:

```bash
mkdir -p /srv/flowbiz/observability/{data,env,logs}
cp stacks/observability/.env.example /srv/flowbiz/observability/env/.env
# Edit /srv/flowbiz/observability/env/.env with real values

cd stacks/observability
export $(grep -v '^#' /srv/flowbiz/observability/env/.env | xargs)
docker compose up -d
```

Or via project Makefile (after adding a helper target):
```bash
make observability-up
```

## Reverse Proxy

Attach Grafana to the existing reverse proxy by provisioning a vhost (example `grafana.flowbiz.ai`). Loki and Prometheus are usually kept internal; if you need remote access, protect endpoints with Basic Auth or VPN.

## Dashboards & Alerts

### Data sources

Two datasources are configured in Grafana (created via the API, stored in the
`obs-grafana-data` volume):

| Name | Type | URL | uid | Scope |
|---|---|---|---|---|
| Loki | loki | `http://loki:3100` | `ffl21vk4eobuoe` | logs from **all 3 VPS** (nginx access/error, journald, vsa-audit) |
| Prometheus | prometheus | `http://prometheus:9090` | `vsa-prometheus` | metrics from **all 3 VPS** (node-exporter host + cAdvisor containers) |

> **Fleet-wide metrics (push model).** The hub scrapes its own node-exporter +
> cAdvisor (stamped `vps_id=vps-01` in `prometheus.yml`). Remote VPS (vps-02/03)
> run the `observability-agent` stack's `prometheus-agent` (agent mode), which
> scrapes their local exporters and **remote-writes** to this Prometheus,
> stamping `vps_id` via `external_labels`. The write path tunnels through the
> `loki.flowbiz.ai` vhost (`/prom/api/v1/write`), reusing its TLS cert, IP
> allow-list and basic auth. Enabled by `--web.enable-remote-write-receiver`
> and Prometheus joining `flowbiz_ext`. Both logs and metrics are now
> fleet-wide; filter any panel with the `VPS` dashboard variable.
>
> To onboard a new VPS's metrics: add its public IP to the `loki.flowbiz.ai`
> vhost allow-list, then deploy the `observability-agent` stack there (see that
> stack's README — including the remote-write password secret step).

### VSA — Fleet Overview dashboard

The primary dashboard (`uid=vsa-fleet-overview`) is generated from a single
source of truth and committed to the repo — do **not** hand-edit the JSON; edit
the generator and regenerate:

```bash
cd stacks/observability/grafana
python3 build_fleet_dashboard.py > dashboards/fleet-overview.json   # regenerate

# Deploy / update in Grafana (admin creds = GRAFANA_ADMIN_USER/PASSWORD from
# the observability .env — currently info@flowbiz.ai):
G="http://<GRAFANA_ADMIN_USER>:<pass>@localhost:3011"   # NB: live host port is 3011, see below
python3 -c "import json;d=json.load(open('dashboards/fleet-overview.json'));\
print(json.dumps({'dashboard':d,'overwrite':True}))" \
  | curl -s -X POST "$G/api/dashboards/db" -H 'Content-Type: application/json' --data @-
```

It has four rows: **Host** (disk per mount, CPU, memory, load, uptime, network),
**Containers** (per-container CPU/memory/network via cAdvisor), **Web traffic**
(req/s, status codes, top domains, methods — from Loki nginx logs), and
**Logs & audit** (log volume by VPS, nginx errors, vsa-audit events). The dashboard
lives in the Grafana DB (editable in the UI, survives restarts); the committed
JSON is the reproducible source if the volume is ever lost.

> **Port gotcha:** `.env` sets `GRAFANA_HTTP_PORT=3010`, but the live container
> currently publishes Grafana on host port **3011** (3010 was already bound).
> Use `docker port observability-grafana-1` to confirm before hitting the API.

You can additionally import community dashboards by ID: 1860 (Node Exporter
Full), 193 / 14282 (cAdvisor), 15190 (NGINX). Configure alert channels as needed.

## Security Notes

- Do **not** expose Loki or Prometheus publicly without authentication.
- Restrict Grafana to VPN/SSO where possible, or enable built-in auth providers.
- Promtail reads `/var/run/docker.sock` – restrict host access accordingly.
- Use short retention for Loki if disk space is limited (adjust in `loki-config.yml`).

## Next Steps

- Add Makefile helpers (`observability-up`, `observability-down`, `observability-logs`).
- Automate provisioning of Grafana dashboards and datasource provisioning under `grafana-provisioning/`.
- Ship Loki to object storage (S3/Infomaniak) for long-term retention.
- Integrate alert rules into Prometheus (`rules/` directory).
