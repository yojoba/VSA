---
name: vsa-fleet
description: Use whenever you need to operate on the FlowBiz VSA fleet — provisioning sites, issuing certs, managing stacks, querying the dashboard, debugging an agent, or pushing changes from the VSA monorepo. Triggers include "deploy this stack", "issue cert for X", "what's running on flowbiz-2", "push to prod", "vsa <anything>", "the dashboard at dashboard.flowbiz.ai", or any infra question scoped to the FlowBiz VSA monorepo.
---

# VSA Fleet — operating manual

This skill is the FAST PATH for working on this repo. The full docs live
under `docs/`, this is the cheat-sheet.

## Hosts (fleet)

| ID | Hostname | Role | IP | SSH |
|---|---|---|---|---|
| `vps-01` | flowbiz-1 | **Hub** (dashboard + obs + dev tenants) | `84.234.20.142` | `ssh -i ~/.ssh/privatekeys/flowbiz-1-fgrosal fgrosal@84.234.20.142` |
| `vps-02` | ov-9a7870 | **LokalFlash prod** | `83.228.221.37`  | `ssh -i ~/.ssh/privatekeys/flowbiz-2 ubuntu@83.228.221.37` |
| `vps-03` | ov-3f2246 | **LokalFlash standby** | `83.228.221.109` | `ssh -i ~/.ssh/privatekeys/flowbiz-3 ubuntu@83.228.221.109` |

Provider: **Infomaniak Public Cloud** (Geneva). The `ov-XXXX` hostnames are
auto-generated OpenStack names — they look like OVH but are NOT.

`fgrosal` (hub) and `ubuntu` (vps-02/03) are in `docker` group + have
NOPASSWD sudo. All `/srv/flowbiz/*` paths are owned by the operator user.

Full details: `docs/runbooks/fleet_access.md`.

## CLI cheat-sheet

```bash
# Sites
vsa site provision --domain X.flowbiz.ai --container Y --port Z
vsa site provision --domain app.lokalflash.ch \
  --route /=lokalflash-frontend:80 \
  --route /api/=lokalflash-pocketbase:8090 \
  --route /_/=lokalflash-pocketbase:8090
vsa site unprovision --domain X [--keep-container] [--keep-cert] [-y]

# Certs — ALWAYS pass --no-www for technical subdomains
vsa cert issue --domain loki.flowbiz.ai --no-www
vsa cert issue --domain newcustomer.com         # OK to include www here
vsa cert renew
vsa cert status

# Auth
vsa auth add --domain X --user Y
vsa auth remove --domain X

# Stacks
vsa stack new NAME
vsa stack up NAME
vsa stack down NAME
vsa stack logs NAME

# VPS fleet
vsa vps list
vsa vps add --id vps-04 --hostname X --ip Y
vsa vps remove vps-04 [-y]

# Agent (run on each VPS)
vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token <T>
vsa agent start                      # one-shot, normally fired by systemd timer

# Alerting (hub only — emails on cert + system + DISK problems, every 15 min)
vsa alert status                     # current problems, no email
vsa alert check [--force|--dry-run]  # the vsa-alert.timer job; emails on change
vsa alert test                       # send a test email to verify SMTP
# Disk alarms (added 2026-06-09) read the hub Prometheus; tune in /etc/vsa/alert.env:
#   VSA_ALERT_DISK_WARN_PERCENT=85  VSA_ALERT_DISK_CRIT_PERCENT=92
#   VSA_ALERT_DISK_MOUNTS=/|/var/lib/docker
```

## Observability / metrics / Grafana (since 2026-06-09)

- **Grafana** = `https://grafana.flowbiz.ai`, admin **`info@flowbiz.ai`** (pwd in
  `stacks/observability/.env` → `GRAFANA_ADMIN_PASSWORD`, gitignored). The live
  container publishes on host port **3011** (not the `.env`'s 3010 — 3010 was
  taken). Datasource UIDs: Loki `ffl21vk4eobuoe`, Prometheus `vsa-prometheus`.
- **Dashboard** `vsa-fleet-overview` is generated from
  `stacks/observability/grafana/build_fleet_dashboard.py` — edit the generator,
  not the JSON, then POST to `/api/dashboards/db`.
- **Fleet-wide metrics = push.** Hub Prometheus has
  `--web.enable-remote-write-receiver` + is on `flowbiz_ext`. vps-02/03 run
  node-exporter + cAdvisor + a `prometheus-agent` (agent mode) in the
  `observability-agent` stack that remote-writes via
  `https://loki.flowbiz.ai/prom/api/v1/write` (reuses the loki vhost's cert +
  allow-list + basic-auth). Password rendered to
  `stacks/observability-agent/secrets/remote_write_password` per host (gitignored).

## Critical conventions

- **`flowbiz_ext`** is the shared Docker network. Every new stack MUST attach
  to it (`networks: { flowbiz_ext: { external: true } }`) so the reverse-proxy
  can route to it.
- **`/srv/<tenant>/<app>/{data,env,logs}/`** is the canonical layout. `.env`
  files in `env/`, `chmod 640`, NEVER committed.
- **vhosts** at `stacks/reverse-proxy/nginx/conf.d/<domain>.conf`, regenerated
  by `vsa vhost sync` from Jinja2 templates + the SQLite registry. Don't
  hand-edit the live ones under `/srv/flowbiz/reverse-proxy/nginx/conf.d/`
  unless you're prototyping a vhost shape that doesn't exist as a template
  yet (then mirror it back into the template).
- **Cert auto-renewal** = certbot container (12 h check) + nginx-reloader
  sidecar (6 h reload). Don't run `certbot` by hand — `vsa cert issue/renew`
  does the right plumbing.
- **Audit dual-write** — every CLI command goes through the `audit()` context
  manager (writes to JSONL + SQLite). Don't bypass it for new commands.
- **Reverse-proxy logs** are JSON per domain, written to
  `/srv/flowbiz/reverse-proxy/logs/domains/<host>.access.json`. Promtail
  parses them.

## Multi-VPS dashboard (since 2026-05-04, see ADR-005)

The dashboard at `https://dashboard.flowbiz.ai` reads `/api/{containers,
domains,certs,stacks}` from agent-synced PostgreSQL tables, NOT from local
Docker/disk. Each response carries `vps_id`. Freshness ~30s (agent tick).

- Schema additions: `ContainerSnapshot.compose_{project,service}`,
  `Certificate.vps_id`, composite `UNIQUE(vps_id, domain)` on `domains` +
  `certificates`.
- Agent `containers-sync` / `domains-sync` / `certs-sync` are scoped by
  `vps_id`. Don't reintroduce unscoped stale-removals.
- Logs from vps-02/03 ship to the hub's Loki via `loki.flowbiz.ai` (basic-auth
  + IP allow-list). New stack: `stacks/observability-agent/`.

To add a new VPS: see `docs/runbooks/observability_agent.md`.

## Bootstrap a fresh VPS

```bash
# Local mac
rsync -az --exclude='.DS_Store' --exclude='__pycache__' \
  ~/dev/github/VSA/ <user>@<IP>:/home/<user>/dev/github/VSA/

# On the new VPS
cd ~/dev/github/VSA
sudo bash infra/scripts/bootstrap_vps.sh
sudo usermod -aG docker $USER          # close+reopen shell

curl -LsSf https://astral.sh/uv/install.sh | sh
cd apps/vps-admin-cli && ~/.local/bin/uv tool install .
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
sudo ln -sf $HOME/.local/bin/vsa /usr/local/bin/vsa     # for systemd

# Permissions for vsa CLI as non-root
sudo mkdir -p /var/log/vsa /var/lib/vsa
sudo chown -R $USER:$USER /var/log/vsa /var/lib/vsa

# Register agent
sudo /usr/local/bin/vsa agent register \
  --hub-url https://dashboard.flowbiz.ai/api \
  --token <ASK_HUB_ADMIN>
echo 'VSA_VPS_ID=vps-0X' | sudo tee -a /etc/vsa/agent.env
sudo cp infra/systemd/vsa-agent.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vsa-agent.timer

# Optional: ship logs to central Loki
infra/scripts/setup_observability_agent.sh
```

## Footguns (each one bit during the 2026-05-04 deploy)

1. **`vsa cert issue` adds www. by default** → fails LE for `loki.*`,
   `dashboard.*`, `grafana.*`, `app.*`, `dev.*`. **Always pass `--no-www`
   for technical subdomains.**
2. **`agent_audit_sync` returns 500** (DataError, ISO timestamp string vs
   datetime). Pre-existing, not fixed yet. Containers/domains/certs sync
   are unaffected.
3. **`dashboard-api` Dockerfile shebang bug** — `.venv/bin/*` scripts point
   at `/workspace/...` (builder path) not `/app/...` (runtime). Run alembic
   via `docker exec dashboard-dashboard-api-1 /app/.venv/bin/python -m
   alembic upgrade head`, not `alembic` directly.
4. **`observability-agent-promtail (unhealthy)`** — healthcheck uses `wget`
   which isn't in `grafana/promtail:3.0.0`. Cosmetic only, logs ship fine.
5. **`/var/log/vsa/` and `/var/lib/vsa/` root-owned by default** on a fresh
   VPS. `vsa` commands as non-root fail with `OperationalError`. Fix with
   `chown -R <user>:<user>` (root can still write through perms when systemd
   runs the agent).
6. **The hub had no local VSA agent** until 2026-05-04. If you stand up a
   new hub, install the agent on it too — the dashboard reads from the same
   table everyone pushes to.
7. **`bootstrap_vps.sh` previously added the WRONG user to docker group**
   (it used `$USER` under `sudo bash` which resolves to `root`). Fixed in
   commit `263c53f` to use `${SUDO_USER:-$USER}`. If you see this on an old
   VPS: `sudo usermod -aG docker <real-user>`.

### Cert-renewal footguns (2026-06-01 incident)

8. **`vsa stack up` honors `COMPOSE_FILE` as of `fd0455f`** (it used to run
   `docker compose -f compose.yml …` and silently drop overrides like
   `compose.dns-cloudflare.yml`, reverting certbot to the plain image). The
   `docker.compose_*` helpers now expand the stack `.env`'s `COMPOSE_FILE` into
   `-f` flags. DNS-01 is active on **vps-02 + vps-03**. **When deploying CLI
   changes, reinstall with `uv tool install . --reinstall --no-cache`** — plain
   `--force` serves a cached wheel because the version stays `0.1.0`.
9. **Orphaned `certbot --dry-run` holds `/etc/letsencrypt/.certbot.lock`** →
   *"Another instance of Certbot is already running"*. Fix: `docker exec
   reverse-proxy-certbot sh -c 'pkill -9 -f dry-run; rm -f
   /etc/letsencrypt/.certbot.lock /var/log/letsencrypt/.certbot.lock'`.
10. **Expiry-only health checks lie about renewal.** A dead ACME account or an
    orphan cert with no vhost makes `certbot renew` fail while the cert still
    reads "valid until X". **`certbot renew --dry-run` is the only honest test**
    — it runs the real challenge per cert. Run it fleet-wide when in doubt.
11. **DNS-01 propagation 30s flakes for apex+www (2026-06-02).** A 2-name cert
    (`apex` + `www`) needs two `_acme-challenge` TXT records to propagate; 30s
    isn't always enough and `certbot renew --dry-run` fails with *"failed to
    verify the DNS TXT records … try increasing
    --dns-cloudflare-propagation-seconds"*. **Default is now 60** (`issue_cert`
    + `vsa cert issue --propagation-seconds`, commit `3580eb5`). Live
    `renewal/*.conf` on vps-02/03 already bumped to 60.
12. **A *batched* `certbot renew --dry-run` can false-fail with `authorization
    must be pending` / malformed** on a couple of certs (authz reused across the
    18-cert batch). NOT a real failure — re-run the suspects with
    `--cert-name <dom> --dry-run` (fresh authz) to confirm.

### Observability / disk footguns (2026-06-09 session)

13. **Root disk full → silent dashboard outage.** vps-01 `/` (19 GB) filled to
    100% → the dashboard's bind-mounted Postgres (`/srv/flowbiz/dashboard/data`)
    crash-looped (`PANIC: No space left on device`) → API 500 on every endpoint.
    Disk fills on root (`/dev/sda1`), NOT the Docker disk (`/dev/sdb`, 246 GB) —
    so `docker system prune` does NOT help. The weight is `~/dev/github`
    node_modules (regenerable — apps run from Docker) + caches. `vsa alert` now
    has a **disk** check so this won't go silent again.
14. **node-exporter network metrics are container-scoped.** It runs in a bridge
    netns, so `node_network_*{device="ens3"}` is empty — it only sees `eth0`.
    Dashboards use cAdvisor `container_network_*` instead (host-mode would expose
    `:9100` publicly on remotes).
15. **Promtail does NOT hot-reload its config — restart it.** The hub Promtail
    ran 3 months without picking up the `vps_id: vps-01` label added to
    `nginx-domain-access`; `docker restart observability-promtail-1` fixed it.
16. **Grafana port is 3011, not the `.env`'s 3010.** And curl basic-auth with an
    `@` in the username (`info@flowbiz.ai`) breaks `http://user:pass@host` URL
    parsing — use `curl -u 'user:pass'`.
17. **A stray host `next dev` can shadow a containerised app.** Found a
    `jobprospectai` Next.js dev server running on the host since March (1.2 GB
    node_modules), while the real site was served by its container — check
    `ps aux | grep dev/github` for orphans before assuming an app is only Dockerised.

### Host load / dockerd footguns (2026-06-19 session)

18. **High host load + `dockerd` pegged but every container's CPU is low → the
    problem is INSIDE dockerd, not a container.** `ps -o etimes,cputimes <dockerd-pid>`
    gives cores-averaged-since-boot; `sudo kill -USR1 <dockerd-pid>` dumps all
    goroutines to `/var/run/docker/goroutine-stacks-*.log` — grep `[running`/`[runnable`
    for what's on-CPU. The 2026-06-19 culprit was a **BuildKit
    provenance/build-history runaway** (123 wedged goroutines recursing a 6.3 GB
    build cache for 129 days = ~1.8 cores). Cleared by a dockerd restart + `docker
    builder prune -af`; prevented with `BUILDX_NO_DEFAULT_ATTESTATIONS=1` in
    `/etc/environment`. (`docker system prune` does NOT touch the BuildKit cache.)
19. **Restart `dockerd` with ZERO container downtime via `live-restore`.** Now on
    in `/etc/docker/daemon.json` (hub). To use: `systemctl reload docker` (SIGHUP,
    no stop) → **verify `docker info --format '{{.LiveRestoreEnabled}}'` = `true`
    BEFORE restarting** → `systemctl restart docker`. Containers keep serving
    (shims + kernel iptables persist); only the Docker API blips a few seconds.
    If `LiveRestoreEnabled` isn't `true`, a restart WILL stop all containers.
20. **cAdvisor is CPU-tuned via compose `command` flags** (`--disable_metrics=…`,
    `--docker_only`, `--housekeeping_interval=30s`) — keeps only cpu/memory/network/
    last_seen. If a new Grafana panel needs another metric family, re-enable it in
    `stacks/observability/compose.yml`. Tuning cAdvisor does NOT affect dockerd CPU
    (it reads cgroups directly, never the Docker socket).

## Don't

- Don't run `docker compose up` directly inside a stack dir — always
  `vsa stack up <name>` so the audit log fires. (As of `fd0455f` it honors the
  `.env` `COMPOSE_FILE` override too, so there's no longer a reason to bypass it
  for DNS-01.)
- Don't hand-edit live nginx vhosts under `/srv/flowbiz/reverse-proxy/nginx/conf.d/`
  unless you also update the corresponding template/registry — `vsa vhost sync`
  will overwrite your hand-edits.
- Don't issue certs manually with `certbot` — `vsa cert issue` does
  registry update + vhost regen + reload.
- Don't commit `.env` files. The repo's `.gitignore` enforces this.
- Don't `vsa vps remove` casually — it cascades into Domain, Certificate,
  ContainerSnapshot, TrafficStat deletions for that VPS.
- Don't push to a remote branch from inside a VPS without checking what
  unpushed local work exists — flowbiz-1 in particular has a `wip/deployer-on-flowbiz1`
  branch with unpushed work (the `apps/deployer/` mini-PaaS — see CLAUDE.md
  "Latest Session" footer for the resume context).

## Where to look

| Question | File |
|---|---|
| What's the architecture? | `docs/architecture.md`, `docs/low-level-design.md` |
| Why was X built that way? | `docs/ADRs/00X-*.md` (especially ADR-005 for multi-VPS) |
| How do I SSH/operate the fleet? | `docs/runbooks/fleet_access.md` |
| How do I add a new VPS to log shipping? | `docs/runbooks/observability_agent.md` |
| How do email alarms work / how to configure? | `docs/runbooks/alerting.md` (config: `/etc/vsa/alert.env`) |
| What's the latest state, footguns, WIP? | `CLAUDE.md` "Latest Session" section (top) |
| What does `vsa <command>` do? | `apps/vps-admin-cli/src/vsa/commands/<command>.py` |
| What does `/api/<endpoint>` do? | `apps/vps-admin-api/src/vsa_api/routers/<endpoint>.py` |
