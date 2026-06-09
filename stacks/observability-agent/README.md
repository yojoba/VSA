# observability-agent

Lightweight stack for **remote VPS** that don't host the central
Loki/Grafana/Prometheus instance. Ships **logs** to the hub's Loki and
**metrics** to the hub's Prometheus, both over HTTPS+basic-auth via
`https://loki.flowbiz.ai` (the metrics path reuses that vhost's cert,
IP allow-list and basic auth — no extra DNS/cert is needed here).

For the **hub VPS** (where Loki + Grafana + Prometheus + the full configs
live), use the regular `observability` stack instead.

## What it collects

**Logs → Loki** (Promtail):

| Source | How |
|---|---|
| systemd journal | `journal:` scrape — sshd, ufw, docker daemon, vsa timer, … |
| nginx per-domain access logs | tail of `/srv/flowbiz/reverse-proxy/logs/domains/*.access.json` (JSON parsed → `domain`, `method`, `status` labels) |
| nginx error logs | tail of `/srv/flowbiz/reverse-proxy/logs/error*.log` |
| vsa CLI audit trail | tail of `/var/log/vsa/audit.jsonl` |
| Every Docker container on the host | `docker_sd_configs` — auto-tags `container`, `compose_project`, `compose_service`, `stream` |

**Metrics → Prometheus** (node-exporter + cAdvisor, scraped locally by a
`prometheus-agent` in agent mode that remote-writes to the hub):

| Source | How |
|---|---|
| Host metrics (CPU, memory, disk, load, network) | `node-exporter` |
| Per-container metrics (CPU, memory, network) | `cAdvisor` |

Every log stream and metric series is stamped with `vps_id` (logs via
Promtail labels, metrics via the agent's `external_labels`) so the
dashboard can filter per VPS.

## Install (per VPS)

```bash
# 1. Make sure the repo is up to date on this VPS
cd ~/dev/github/VSA && git pull

# 2. Configure
sudo mkdir -p /srv/flowbiz/observability-agent/env
cd stacks/observability-agent
cp .env.example .env
$EDITOR .env       # set VSA_VPS_ID, LOKI_BASIC_AUTH_USER, LOKI_BASIC_AUTH_PASSWORD

# 3. Render the remote-write password secret (Prometheus can't read it from
#    an env var). Host-only, gitignored, mode 644 so the container user reads it.
mkdir -p secrets
grep LOKI_BASIC_AUTH_PASSWORD .env | cut -d= -f2- | tr -d '\n' > secrets/remote_write_password
chmod 644 secrets/remote_write_password

# 4. Make sure the reverse-proxy stack is logging in JSON format and
#    bind-mounting /srv/flowbiz/reverse-proxy/logs (it should be by
#    default — see stacks/reverse-proxy/compose.yml). If not:
vsa stack up reverse-proxy

# 5. Start the agent (promtail + node-exporter + cadvisor + prometheus-agent)
vsa stack up observability-agent

# 6. Verify it's pushing
docker logs observability-agent-promtail --tail 20    # 'Successfully sent batch'
docker logs observability-agent-prometheus --tail 20  # 'Server is ready', no 4xx on remote_write
```

> The hub side must be prepared once: Prometheus runs with
> `--web.enable-remote-write-receiver` and joins `flowbiz_ext`, and the
> `loki.flowbiz.ai` vhost has a `/prom/` location proxying to it. A new VPS
> only needs its public IP added to that vhost's allow-list (it shares the
> loki one). See `stacks/observability/README.md`.

## Verify on the hub

```
# In Grafana (https://grafana.flowbiz.ai)
{vps_id="vps-02"} |= ""                              # all logs from vps-02
{vps_id="vps-02", job="nginx-domain-access"} | json  # only nginx
{container="lokalflash-pocketbase"}                  # one specific container
```

## Troubleshoot

| Symptom | Likely cause | Fix |
|---|---|---|
| `tcp: lookup loki.flowbiz.ai: no such host` | DNS A record missing | Check `dig +short A loki.flowbiz.ai` returns the hub IP |
| 401 Unauthorized | basic-auth credentials wrong | Verify `.env` matches the entry in `/etc/nginx/auth/loki.flowbiz.ai.htpasswd` on the hub |
| 403 Forbidden | source IP not in allow-list | Add this VPS's public IP to the `allow ...;` block in `loki.flowbiz.ai.conf` on the hub |
| Empty Grafana queries | nginx logging in non-JSON format on this host | Confirm `access_log` directives in `/srv/flowbiz/reverse-proxy/nginx/conf.d/*.conf` use `json_detailed` |
| `${VSA_VPS_ID}` shown literally as a label | `-config.expand-env=true` flag missing | Check `compose.yml` `command:` block |
