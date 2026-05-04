# Fleet Access Runbook

How to SSH into and operate every VPS in the FlowBiz fleet. **Provider:
Infomaniak Public Cloud** (the `ov-XXXXXX` hostnames are auto-generated
OpenStack names — they look like OVH but are NOT). Everything is in
Geneva (CH).

## Hosts

| ID       | Hostname (OS) | Role                                                                       | Specs            | Public IP        |
| -------- | ------------- | -------------------------------------------------------------------------- | ---------------- | ---------------- |
| `vps-01` | `flowbiz-1`   | **Hub** — VSA dashboard + Loki/Grafana + dev tenants (LokalFlash dev, n8n, dify, electroziles, naturalpes, jobprospectai, ...) | mutualised       | `84.234.20.142`  |
| `vps-02` | `ov-9a7870`   | **LokalFlash prod active** — `app.lokalflash.ch` + `lokalflash.ch` + `.com` | 4 vCPU / 8 GB / 154 GB | `83.228.221.37`  |
| `vps-03` | `ov-3f2246`   | **LokalFlash prod warm standby** — Litestream pull, master pre-built       | 2 vCPU / 4 GB / 58 GB  | `83.228.221.109` |

## SSH

The private keys live in `~/.ssh/privatekeys/` on the operator's mac. They
are NOT in this repo. The corresponding public keys are in `~/.ssh/authorized_keys`
on each VPS (deposited at provisioning time).

```bash
# Hub (flowbiz-1)
ssh -i ~/.ssh/privatekeys/flowbiz-1-fgrosal fgrosal@84.234.20.142

# LokalFlash prod (flowbiz-2)
ssh -i ~/.ssh/privatekeys/flowbiz-2 ubuntu@83.228.221.37

# LokalFlash standby (flowbiz-3)
ssh -i ~/.ssh/privatekeys/flowbiz-3 ubuntu@83.228.221.109
```

For per-VPS ergonomics, add to `~/.ssh/config` on the operator's mac:

```ssh-config
Host flowbiz-1
  HostName 84.234.20.142
  User fgrosal
  IdentityFile ~/.ssh/privatekeys/flowbiz-1-fgrosal

Host flowbiz-2
  HostName 83.228.221.37
  User ubuntu
  IdentityFile ~/.ssh/privatekeys/flowbiz-2

Host flowbiz-3
  HostName 83.228.221.109
  User ubuntu
  IdentityFile ~/.ssh/privatekeys/flowbiz-3
```

Then `ssh flowbiz-1`, `ssh flowbiz-2`, `ssh flowbiz-3`.

## sudo state

| VPS | Operator user | Member of `docker`? | sudo NOPASSWD? |
| --- | --- | --- | --- |
| flowbiz-1 | `fgrosal` | ✅ | ✅ (since 2026-05-04, file `/etc/sudoers.d/fgrosal-nopasswd`) |
| flowbiz-2 | `ubuntu`  | ✅ (default cloud-init) | ✅ (default cloud-init) |
| flowbiz-3 | `ubuntu`  | ✅ (default cloud-init) | ✅ (default cloud-init) |

All `/srv/flowbiz/*` directories are owned by the operator user, so most ops
don't need sudo at all.

## Conventional paths

```
/etc/vsa/agent.env                       — VSA agent config (root:root, 640)
                                            VSA_HUB_URL, VSA_AGENT_TOKEN, VSA_VPS_ID
/etc/systemd/system/vsa-agent.service    — agent oneshot unit (User=root)
/etc/systemd/system/vsa-agent.timer      — 30-second tick
/var/log/vsa/audit.jsonl                 — JSONL audit trail (consumed by Promtail)
/var/lib/vsa/audit.db                    — SQLite mirror (queried by hub merge)
/var/lib/vsa/agent_sync_state.json       — last_id checkpoint per sync
/srv/flowbiz/                            — root for all stack data + config
├── reverse-proxy/
│   ├── nginx/conf.d/<domain>.conf       — nginx vhosts (managed by `vsa site provision`)
│   ├── nginx/auth/<domain>.htpasswd     — basic-auth bcrypt files
│   ├── nginx/snippets/                  — log_format_json, security_headers, rate_limit
│   ├── letsencrypt/live/<domain>/       — LE certs (writable by operator user!)
│   ├── certbot-www/                     — ACME challenge webroot
│   └── logs/
│       ├── access.log, error.log        — global nginx logs (combined format)
│       └── domains/<domain>.access.json — per-domain JSON logs (parsed by Promtail)
├── dashboard/data/postgres/             — dashboard-api PG data
├── observability/data/grafana-provisioning/  — Grafana provisioning
├── observability-agent/env/.env         — Promtail credentials (on vps-02 + vps-03 only)
└── deployer/                            — WIP mini-PaaS data (flowbiz-1 only, branch wip/deployer-on-flowbiz1)
```

`/var/lib/docker/volumes/` lives on a separate disk on flowbiz-1 (mutualised
host with many tenants), single disk on flowbiz-2/3.

## VSA agent state

Verify the agent is alive on a given VPS:

```bash
ssh <host> 'systemctl status vsa-agent.timer && journalctl -u vsa-agent.service --since "5 minutes ago" --no-pager | tail -10'
```

Expected output: `Active: active (waiting)` on the timer, and recent entries
in the journal showing `✓ Heartbeat`, `✓ Containers`, `✓ Certificates`,
`✓ Domains`, `✓ Traffic stats`. The `Audit events` line currently shows
`✗ 500 Internal Server Error` — pre-existing bug, see CLAUDE.md footgun #2.

Force an immediate sync (don't wait for the next 30s tick):

```bash
ssh <host> 'sudo systemctl start vsa-agent.service'
```

## Loki access (Grafana)

- **URL:** `https://grafana.flowbiz.ai`
- **Default user:** `admin`
- **Default password:** `change_me` (in `stacks/observability/.env`) — **rotate**
  on first use via Profile → Change Password, or by patching the `.env` file
  on flowbiz-1 then `vsa stack up observability`.

Useful Loki queries from any panel/explore tab:

```logql
{vps_id="vps-02"} |= ""                        # everything from LokalFlash prod
{vps_id="vps-02", job="nginx-domain-access"}   # only nginx logs
{container="lokalflash-pocketbase"}            # one specific container
{vps_id="vps-01", compose_project="dify"}      # all dify containers on the hub
```

The Loki push endpoint at `https://loki.flowbiz.ai/loki/api/v1/push` is
behind `auth_basic` + IP allow-list (vps-01, vps-02, vps-03). Credentials
are in `/srv/flowbiz/observability-agent/env/.env` on each remote agent VPS.

## Quick health check (one-liner)

```bash
for h in flowbiz-1 flowbiz-2 flowbiz-3; do
  echo "=== $h ==="
  ssh "$h" 'docker ps --format "table {{.Names}}\t{{.Status}}" | head -15'
done
```

## When something is broken

1. **Container down on a remote VPS** — SSH there, `docker logs <name> --tail 50`,
   then `docker restart <name>`. Or escalate to `vsa stack up <stack>` to recreate.
2. **`/api/agent/*-sync` returning errors** — check `docker logs dashboard-dashboard-api-1 --tail 50` on flowbiz-1.
   The `audit-sync` 500 is expected (pre-existing bug); other 500s are real.
3. **Cert expiring soon** — `vsa cert renew` on the hub triggers a renew for all certs.
   The auto-renew sidecar runs every 12 h.
4. **Loki disk filling up** — check `docker exec observability-loki-1 df -h /loki` on flowbiz-1.
   Retention is 30 days. To temporarily reduce: edit `stacks/observability/loki-config.yml`
   `limits_config.retention_period`, then `vsa stack up observability`.
5. **`vsa` CLI broken with `ImportError`** — re-install: `cd ~/dev/github/VSA/apps/vps-admin-cli && uv tool install . --reinstall`.
   May need `sudo chown -R <user>:<user> ~/.local/share/uv` first if pyc files were created by root.
