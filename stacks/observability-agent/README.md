# observability-agent

Lightweight Promtail-only stack for **remote VPS** that don't host the
central Loki/Grafana instance. Ships logs over HTTPS+basic-auth to the
hub's Loki at `https://loki.flowbiz.ai`.

For the **hub VPS** (where Loki + Grafana + the full Promtail config
live), use the regular `observability` stack instead.

## What it collects

| Source | How |
|---|---|
| systemd journal | `journal:` scrape — sshd, ufw, docker daemon, vsa timer, … |
| nginx per-domain access logs | tail of `/srv/flowbiz/reverse-proxy/logs/domains/*.access.json` (JSON parsed → `domain`, `method`, `status` labels) |
| nginx error logs | tail of `/srv/flowbiz/reverse-proxy/logs/error*.log` |
| vsa CLI audit trail | tail of `/var/log/vsa/audit.jsonl` |
| Every Docker container on the host | `docker_sd_configs` — auto-tags `container`, `compose_project`, `compose_service`, `stream` |

Every stream is also stamped with `vps_id` so the dashboard can filter
per VPS.

## Install (per VPS)

```bash
# 1. Make sure the repo is up to date on this VPS
cd ~/dev/github/VSA && git pull

# 2. Configure
sudo mkdir -p /srv/flowbiz/observability-agent/env
cd stacks/observability-agent
cp .env.example .env
$EDITOR .env       # set VSA_VPS_ID, LOKI_BASIC_AUTH_USER, LOKI_BASIC_AUTH_PASSWORD

# 3. Make sure the reverse-proxy stack is logging in JSON format and
#    bind-mounting /srv/flowbiz/reverse-proxy/logs (it should be by
#    default — see stacks/reverse-proxy/compose.yml). If not:
vsa stack up reverse-proxy

# 4. Start the agent
vsa stack up observability-agent

# 5. Verify it's pushing
docker logs observability-agent-promtail --tail 20
# Look for: 'Successfully sent batch' — no auth/network errors.
```

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
