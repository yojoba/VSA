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

1. Log into Grafana (default admin credentials from `.env`).
2. Add data sources:
   - Loki (`http://loki:3100`)
   - Prometheus (`http://prometheus:9090`)
3. Import dashboards (suggested IDs):
   - 1860 (Node Exporter Full)
   - 179 (Prometheus 2.0 Stats)
   - 15190 (NGINX Overview)
   - 193 (cAdvisor Exporter)
4. Configure alerting channels as needed (Slack, email, etc.).

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
