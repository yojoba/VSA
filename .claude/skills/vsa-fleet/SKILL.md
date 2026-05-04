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
```

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

## Don't

- Don't run `docker compose up` directly inside a stack dir — always
  `vsa stack up <name>` so the audit log fires.
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
| What's the latest state, footguns, WIP? | `CLAUDE.md` "Latest Session" section (top) |
| What does `vsa <command>` do? | `apps/vps-admin-cli/src/vsa/commands/<command>.py` |
| What does `/api/<endpoint>` do? | `apps/vps-admin-api/src/vsa_api/routers/<endpoint>.py` |
