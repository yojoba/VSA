# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Latest Session — 2026-06-19 (vps-01 high-load investigation → chronic dockerd/BuildKit peg killed, zero downtime)

> **Resume context.** Triggered by "verifie le load de vps01" — load was **5.10
> on 4 vCPU** (~128%, sustained, not a spike). The investigation peeled back
> three layers; the real culprit was a **129-day chronic `dockerd` CPU peg**.
> **Read before touching:** the hub's `/etc/docker/daemon.json`, the
> `observability` cadvisor service, or anything that runs `docker compose build`
> on the fleet.

**Root cause (the big one): BuildKit provenance/build-history runaway.** A
SIGUSR1 goroutine dump of `dockerd` (`kill -USR1 <MainPID>` → written to
`/var/run/docker/goroutine-stacks-*.log`) showed **123 goroutines wedged in
`moby/buildkit/solver/llbsolver.recordBuildHistory` → `ProvenanceCreator.Predicate`
→ `exporter.ExportTo` (deep recursion) → bbolt cache reads**, plus 28 more
blocked on the bbolt mutex they monopolized. `dockerd`'s cpu-time/elapsed ratio
was **1.79 cores averaged over the full 129-day uptime** — i.e. it had been
burning ~1.8 cores continuously *since boot*, not recently. The 6.3 GB BuildKit
cache was the graph these goroutines looped over; every `docker compose up
--build` (this fleet builds images locally a lot) fed it.

**The fix — ZERO container downtime via `live-restore`.** Wedged goroutines can
only be cleared by restarting the `dockerd` *process*. To do it without dropping
the ~30 running containers:
1. Wrote `/etc/docker/daemon.json` = `{"live-restore": true}` (was `{}`).
2. `systemctl reload docker` (SIGHUP — does **not** stop containers) to arm
   live-restore on the running daemon.
3. **Gate:** verified `docker info --format '{{.LiveRestoreEnabled}}'` = `true`
   *before* restarting (if not true, abort — restart would kill containers).
4. `systemctl restart docker` → fresh daemon, containers kept running (shims +
   kernel iptables persist), goroutines died with the old process. Verified
   `docker ps -q | wc -l` = 30 before **and** after; dashboard/sites stayed 200.
5. `docker builder prune -af` → reclaimed the 6.3 GB cache.

**Result:** `dockerd` **178% → 0.5–1.0% steady**; load 1-min **5.10 → 1.31**.
A transient ~116% right after the restart was just the 40 log-tail streams
re-reading their backlog (one-shot), which settled.

**Prevention (so it doesn't slowly rebuild):** added
`BUILDX_NO_DEFAULT_ATTESTATIONS=1` to the hub's `/etc/environment` — disables the
default provenance attestations whose history records wedged dockerd. `live-restore`
in `daemon.json` is now permanent (all future daemon restarts/crashes are
container-safe).

**Secondary contributor (left as-is, it's real work):** Promtail tails **all**
container logs through the Docker API (`observability/promtail-config.yml`
`docker_sd_configs` → `unix:///var/run/docker.sock`), creating ~40
`loggerutils.tailFiles`/`ReadLogs` goroutines in dockerd. A few % — normal cost
of shipping logs to Loki. Could later switch to file-based tailing
(`/var/lib/docker/containers/*/*.log`) to bypass dockerd entirely, at the cost
of re-deriving container labels.

**Also shipped this session (committed):**
- **cAdvisor CPU tuned** (`stacks/observability/compose.yml`). It ran with **no
  command flags** = worst-case defaults (dozens of metric groups every 1s on
  every cgroup). Added `--housekeeping_interval=30s`, `--docker_only=true`, and
  `--disable_metrics=disk,diskIO,tcp,udp,advtcp,sched,process,hugetlb,referenced_memory,cpu_topology,resctrl,percpu,memory_numa,perf_event`.
  CPU **6–7% → 0.15%**. Keeps only `cpu`/`memory`/`network`/`last_seen` — the 4
  families the Grafana **VSA — Fleet Overview** dashboard actually queries
  (verified: 31/31/53/31 series still present, Prometheus `up{job="cadvisor"}=1`).
  NOTE: cAdvisor reads cgroups from `/sys` + `/var/lib/docker` directly — it does
  **not** touch the Docker socket, so this helped cAdvisor's own CPU but had zero
  effect on the dockerd peg (a red herring early in the investigation).

**Host-side / not in this repo (by design):**
- Stopped+removed the **`deployer` container** (the abandoned WIP
  `wip/deployer-on-flowbiz1` git-pull-and-redeploy mini-PaaS at
  `deploy.flowbiz.ai`). It was up 3 months, leaked **13 zombie `git`** children
  (spawned-but-never-reaped), and hadn't deployed anything since Mar 4. Only
  `db`/0.13% CPU — **not** a load driver, just zombies + attack surface. Its
  vhost/cert at `deploy.flowbiz.ai` are now dangling (nginx 502s, harmless) —
  not yet unprovisioned.
- **ghost-docker** (separate repo `~/dev/github/ghost-docker`, not VSA): its
  `db` healthcheck ran `mysqladmin ping` every **1s** (`interval: 1s`,
  `retries: 120`) → constant `docker exec` churn. Bumped to `30s`/`5`.

**Footguns (don't repeat):**
1. **`dockerd` high CPU with low per-container CPU = look inside dockerd, not at
   containers.** `ps -o etimes,cputimes` on the dockerd PID gives the
   averaged-since-boot core count; `kill -USR1 <pid>` dumps goroutines to
   `/var/run/docker/goroutine-stacks-*.log` — grep for `[running`/`[runnable`
   stacks, that's what's on-CPU.
2. **To restart dockerd without downtime you MUST enable `live-restore` and
   verify `LiveRestoreEnabled=true` BEFORE the restart.** Enabling it via reload
   alone is not enough proof — check `docker info`.
3. **The host VSA checkout is `~/dev/github/VSA` (uppercase V-S-A)**, not
   `vsa`. SSH alias for vps-01 is `flowbiz-1` (`.ch` prod = `flowbiz-2/3`).
4. **`docker system prune` does not touch BuildKit cache** — use `docker builder
   prune -af`. Build cache lives on `/dev/sdb`, so it doesn't relieve root `/`.

**WIP not in repo:** none from this session pushed yet — the commit
(cadvisor + this note) is **local on master, not pushed**. Host already runs the
tuned cadvisor (file scp'd + recreated). `daemon.json`, `/etc/environment`, and
the ghost-docker edit are host-only by design.

## Latest Session — 2026-06-09 (disk-full outage → fleet-wide metrics + Grafana dashboard + disk alarms)

> **Resume context.** Started from a broken `dashboard.flowbiz.ai/health`.
> Root cause: **vps-01 root disk (`/dev/sda1`, 19 GB) was 100% full**, which
> crash-looped the dashboard's bind-mounted Postgres
> (`/srv/flowbiz/dashboard/data`, `PANIC: No space left on device`) → API 500.
> That one incident pulled in four pieces of work, all shipped. **Read before
> touching:** `stacks/observability*`, `services/agent_sync.py`,
> `services/alerting.py`, the `loki.flowbiz.ai` vhost, or the Grafana setup.

**What landed (commits `02c3bbe` → latest, ~12 commits):**

- **Disk incident fixed.** Purged regenerable caches + `~/dev/github` node_modules
  (apps run from Docker, not these checkouts) → root disk **100% → 65%**. Also
  killed an orphaned **host `next dev` for jobprospectai** (running since
  2026-03-04, 1.2 GB node_modules) — the real site is served by its *container*
  `jobprospectai:3000`; the host process listened on nothing. `docker system
  prune` does NOT help: Docker lives on `/dev/sdb` (246 GB), the pressure is on
  root `/`.

- **Agent heartbeat IP fix (`02c3bbe`).** `collect_heartbeat` used
  `gethostbyname(gethostname())` → `127.0.0.1` on the hub (`/etc/hosts` maps the
  hostname to loopback), so the dashboard showed vps-01 as `127.0.0.1`. Now uses
  the primary egress IP (UDP-socket trick) + optional `VSA_VPS_IP` override.

- **Grafana dashboard.** Added the **Prometheus** datasource (`vsa-prometheus`),
  fixed the **Loki** datasource (its URL was empty), and built **VSA — Fleet
  Overview** (`uid=vsa-fleet-overview`) from
  `stacks/observability/grafana/build_fleet_dashboard.py` (single source of
  truth — edit the generator, not the JSON). Grafana admin is now
  **`info@flowbiz.ai`** (was `admin`/`change_me`), live host port **3011**
  (`.env` says 3010 — stale). See [[fleet-metrics-grafana]] memory.

- **Fleet-wide metrics (push model).** node-exporter/cAdvisor metrics now cover
  all 3 VPS. Hub Prometheus got `--web.enable-remote-write-receiver` + joined
  `flowbiz_ext`; vps-02/03 run a `prometheus-agent` (agent mode) in the
  `observability-agent` stack that remote-writes through the **`loki.flowbiz.ai`
  vhost's new `/prom/` location** (reuses cert + IP allow-list + basic-auth, no
  new DNS/cert on remotes). Remote-write password is host-only at
  `stacks/observability-agent/secrets/remote_write_password` (gitignored).

- **`vsa alert` disk alarms.** New `disk` category queries the hub Prometheus
  (`node_filesystem_*`, all VPS) and warns ≥85% / critical ≥92% per mount
  (`VSA_ALERT_DISK_{WARN,CRIT}_PERCENT`, `VSA_ALERT_DISK_MOUNTS`). 87 CLI tests
  pass. Runbook + `alert.env.example` updated.

**New footguns (don't repeat):**

1. **Root-disk-full is a silent killer** — it crash-loops the bind-mounted
   dashboard Postgres before any expiry/health check notices. The new `vsa
   alert` disk check is the guard; `df -h /` (not `docker system df`) is the
   first thing to run if the dashboard 500s.
2. **node-exporter network metrics are container-scoped** (bridge netns → only
   `eth0`, never host `ens3`). Network panels use cAdvisor `container_network_*`.
3. **Promtail doesn't hot-reload** — `docker restart observability-promtail-1`
   after editing `promtail-config.yml`. The hub ran 3 months missing the
   `vps_id` label on nginx logs because it was never restarted.
4. **Grafana on port 3011, not 3010**, and `curl -u 'user:pass'` (not
   `http://user:pass@host`) when the username has an `@` (`info@flowbiz.ai`).
5. **Each Grafana panel needs a panel-level `datasource`** or PromQL panels
   error to "No data" (they fall back to the default Loki datasource).

**WIP not in repo:** none — all pushed to master. Host-only (gitignored, by
design): `stacks/observability/.env` (Grafana creds), each remote's
`observability-agent/secrets/remote_write_password`, `/etc/vsa/alert.env`.

## Latest Session — 2026-06-02 → 06-03 (DNS-01 propagation fix; renewal verified healthy)

> **Resume context.** Triggered by cert-expiring email alarms. Outcome: the
> auto-renewal is **working** — the alarms were nominal (certs crossed the 30d
> warning threshold, certbot renewed them, recovery email sent). A fleet-wide
> `certbot renew --dry-run` then surfaced one real latent issue (below) and a
> benign artifact. **Read before touching:** `services/certbot.py`,
> `commands/cert.py`, or any DNS-01 `renewal/*.conf`.

**What happened.** On 2026-06-02 the hub emailed 7 `cert-expiring-soon` warnings
(`dify`, `flowbiz.ai`, `grafana`, `handsome`, `n8n`, `portfoliomanager`,
`raphaelpittier.com` — all vps-01 HTTP-01 webroot). They crossed the 30-day
threshold (= exactly when LE allows renewal); certbot's 12h loop renewed all 7
(valid to Aug 31) and `vsa alert` emailed "7 resolved" at 20:00. **Not a
failure** — the system working as designed. The `All renewals failed …
lokalflash.com` lines in `docker logs --tail` are **stale history** (container
up 3 months) from before the 2026-06-01 orphan cleanup; those 3 certs are gone
from disk + the container.

**Real finding (fixed): DNS-01 propagation 30s too low for apex+www.** A
fleet-wide `--dry-run` found `lokalflash.ch`+www **failing on vps-02** (*"failed
to verify the DNS TXT records … try increasing
--dns-cloudflare-propagation-seconds (currently 30 seconds)"*) while single-name
`app.lokalflash.ch` passed and vps-03 passed both — 30s flakes once two
`_acme-challenge` TXT records must propagate.
- **Host fix:** bumped `dns_cloudflare_propagation_seconds = 30 → 60` in the live
  `renewal/{lokalflash.ch,app.lokalflash.ch}.conf` on **vps-02 + vps-03**;
  targeted re-dry-run then passed.
- **Code fix (`3580eb5`, pushed + deployed to all 3 VPS):** `issue_cert`'s
  default `propagation_seconds` is now **60** (covers `cert.py`, `site.py`
  provision/`--standby`, `fleet site-provision`) and `vsa cert issue` gained
  `--propagation-seconds`, so a reissue no longer reverts the conf to 30s.

**Benign artifact (no action):** a *batched* `certbot renew --dry-run` (18 certs)
false-failed `loki.flowbiz.ai` + `jobprospectai.flowbiz.ai` with `authorization
must be pending` (authz reuse across the batch). Targeted `--cert-name X
--dry-run` on each succeeded. Their port-80 vhosts serve
`/.well-known/acme-challenge/` fine; the IP allow-list is only on the 443 block.

**Final state:** all 3 VPS on commit `3580eb5`; `certbot renew --dry-run` green
fleet-wide; `vsa fleet drift` = 0/0/0; no orphaned certbot locks. Only the 2
known cosmetic-unhealthy containers remain (jobprospectai, lokalflash-website).
**WIP not in repo:** none — the host `renewal/*.conf` edits are gitignored by
design.

## Latest Session — 2026-06-01 (cert auto-renewal incident: vps-02 → DNS-01)

> **Resume context.** This session fixed a **silent prod cert-renewal outage**
> and verified auto-renewal fleet-wide. **Read this before touching:** the
> reverse-proxy certbot setup on any VPS, `services/certbot.py`, the
> `compose.dns-cloudflare.yml` override, or `vsa stack up`.

**What happened.** The dashboard Fleet Health page showed 4 critical
`cert-expiring-soon` findings. Root causes were two *different* problems:

- **vps-02 (LokalFlash prod)** — `certbot renew` had been failing every 12h
  since March with *"Account at .../acme-**v01**.../... does not exist"*: the
  `.ch` renewal confs referenced a **dead ACME v1 account**. The prod certs had
  silently reached **J-8**. (The standby vps-03 was fine — already on DNS-01.)
  **Fix:** migrated vps-02 to **DNS-01** (mirroring vps-03): copied the CF token
  to `cloudflare.ini`, set the `COMPOSE_FILE` override, recreated *only* the
  certbot container, reissued `lokalflash.ch`(+www) and `app.lokalflash.ch`
  (`--no-www`) via `vsa cert issue --challenge dns-cloudflare`. Valid to Aug 30,
  fresh ACME v2 account, `--dry-run` confirms sustained renewal.
- **vps-01 (hub)** — stale orphan certs whose domains no longer resolve there:
  `lokalflash.com`/`www`/`app.lokalflash.com` (**no DNS at all** — decommissioned),
  `app.lokalflash.ch` (migration orphan; real cert lives on vps-02/03), and
  `electroziles.flowbiz.ai` (orphan cert with **no vhost** → HTTP-01 challenge
  hit the default server and failed). All removed via
  `vsa site unprovision … --keep-container -y` (the LokalFlash `lokalflash.com`
  vhost shared `lokalflash-website` with the live **dev** env — footgun #4 — so
  `--keep-container` was mandatory). `lokalflash.com` assignment removed from the
  registry too (`vsa fleet remove lokalflash.com -y`).

**Verification.** `certbot renew --dry-run` on all 3 VPS: vps-01 **18/18**
(HTTP-01 webroot), vps-02 **2/2** (DNS-01), vps-03 **2/2** (DNS-01) — every
active cert confirmed auto-renewable. `vsa fleet drift` = **0 critical / 0
warning / 0 info**.

**DNS-01 is now active on vps-02 + vps-03** (was vps-03 only).

**New footguns (don't repeat):**

1. **`vsa stack up` used to IGNORE `COMPOSE_FILE` — FIXED in `fd0455f`.** It ran
   `docker compose -f compose.yml …`, which makes docker compose ignore the
   `COMPOSE_FILE` env var, silently dropping the `compose.dns-cloudflare.yml`
   override (so `vsa stack up`/`vsa site provision` reverted certbot to the
   plain image on DNS-01 VPS). The `docker.compose_*` helpers now read the
   stack's `.env` themselves and expand `COMPOSE_FILE` into multiple `-f` flags.
   After pulling, **reinstall the CLI with `--no-cache`** (see footgun 5 below).
2. **A disconnected `docker exec … certbot renew --dry-run` leaves an orphaned
   certbot process holding `/etc/letsencrypt/.certbot.lock`** → subsequent runs
   (and the 12h loop) fail with *"Another instance of Certbot is already
   running"*. Fix: `pkill -9 -f dry-run` inside the container + `rm -f
   /etc/letsencrypt/.certbot.lock /var/log/letsencrypt/.certbot.lock`.
3. **An orphan cert with no vhost silently fails HTTP-01 renewal** (challenge
   falls through to the default server). `certbot renew --dry-run` is the only
   reliable way to surface these before they expire — `cert status`/days-left
   won't tell you the *renewal path* is broken.
4. **`certbot renew` reports the dead-v1-account error as a renewal failure, not
   an expiry warning** — so a cert can be days from expiry while every health
   check that only reads expiry dates still looks "valid until X". Watch the
   certbot container logs, not just expiry.
5. **Deploying a CLI change: `uv tool install . --force` serves a CACHED wheel
   when the version is unchanged** (still `0.1.0`) — your new code silently
   doesn't land. Use `uv tool install . --reinstall --no-cache` (verify with
   `grep -c <new-symbol> ~/.local/share/uv/tools/vsa-cli/.../<file>.py`).
6. **On the hub, `__pycache__` under `~/.local/share/uv/tools/vsa-cli` is
   root-owned** (the root agent wrote `.pyc` there before `PYTHONDONTWRITEBYTECODE=1`),
   so a user `uv tool install` fails to remove it and leaves deps half-installed
   (`module 'click' has no attribute 'command'`). Fix: `sudo chown -R $USER:$USER
   ~/.local/share/uv && uv tool uninstall vsa-cli && uv tool install . --no-cache`.

**Email alerting (`vsa alert`, commit `c6a715b`).** New `vsa alert` command
group + `vsa-alert.timer` (every 15 min on the hub) that emails alarms for cert
**and** system problems. `vsa alert check` queries the hub
(`/fleet/drift` + `/vps` + `/containers`), collects problems — cert
expiry/drift, agents that stopped reporting (`last_seen` stale), down/unhealthy
containers — at/above `VSA_ALERT_MIN_LEVEL`, and emails a digest. It dedups via
`/var/lib/vsa/alert-state.json` so it only emails **on change** (new/escalated
problem, or full recovery), never spamming. SMTP over STARTTLS, stdlib-only.
Config in `/etc/vsa/alert.env` (mode 600, gitignored) — currently
`info@lokalflash.ch` via Infomaniak → `alexandre@netcool.ch` +
`info@flowbiz.ai`, min level `warning`. `vsa alert {status,test,check
--dry-run}` for ops. Runbook: `docs/runbooks/alerting.md`. To silence a known
cosmetic-unhealthy container, add its name to `VSA_ALERT_IGNORE_CONTAINERS`.

**WIP not in repo yet:** none from this session. The DNS-01 enablement on vps-02
is config-only (`cloudflare.ini` + `stacks/reverse-proxy/.env`, both gitignored)
— not committed by design. `/etc/vsa/alert.env` (SMTP password) is likewise
host-only, never committed.

---

## Latest Session — 2026-05-04 (Phase A→E: full multi-VPS write-side)

> **Resume context.** A second session on 2026-05-04 took VSA from "mono-VPS
> write tool + read-only multi-VPS dashboard" to **fully fleet-aware on both
> read and write sides.** Phases A→E shipped end-to-end. **Read this before
> touching:** `routers/{assignments,fleet,agent}.py`, anything under `commands/fleet.py`,
> `services/{hub_client,certbot,agent_sync}.py`, the `compose.dns-cloudflare.yml`
> override, or migrations 0005/0006/0007.

**What landed (commits `77bdb82` → `16a7ffb`, 17 commits):**

- **Phase A** (`e323ba3`) — `vsa cert health` (diagnostic: broken symlinks,
  missing LE accounts, expiring certs; exits 1 on any critical) + `vsa cert
  issue --challenge dns-cloudflare` (DNS-01 wrapper) + `vsa cert status`
  fixed to read host filesystem (was using `docker exec nginx openssl` which
  silently returned empty because alpine has no openssl).

- **Phase B** (`183a62e` + `a6ae9a9`) — `domain_assignments` registry: new
  table + Pydantic model + 4 REST endpoints (GET list / GET one / PUT
  upsert / DELETE), `vsa fleet assign|list|show|remove|backfill` CLI, new
  `/assignments` page in the dashboard. Backfilled 21 assignments live.

- **Phase C** (`20bf6d7`) — Hub→agent execution channel. New
  `agent_commands` queue + 5 agent endpoints (`exec`, `commands` list,
  `commands/{id}` get, `take`, `result`). Agent gained a `sync_pending_commands`
  step in its 30s loop that pulls pending rows for its own `vps_id`, atomic
  takes via `/take` (409 if already taken), runs `subprocess.run(["vsa",
  *argv], …)`, captures stdout/stderr (64 KB cap), POSTs result. CLI
  `vsa fleet exec --vps X -- …` enqueues + polls + streams output.

- **Phase D** (`7c56c63`) — Orchestrator `vsa fleet site-provision --domain
  X --primary Y --standbys Z[,W] --container c --port p`: runs full
  `vsa site provision` on primary then `vsa site provision … --standby`
  on each standby (skip container attach, DNS-01 cert, no HTTP-only ACME
  vhost, otherwise identical), then writes the assignment row. Convenience
  wrappers: `vsa fleet vhost-sync`, `cert-renew`, `cert-health [--all]`.

- **Phase E** (`8ea54bb` + `bd7f9b4`) — `/api/fleet/drift` cross-checks
  intent vs observed: `missing-on-primary` / `missing-on-standby` /
  `rogue-host` / `cert-missing` / `cert-expiring-soon` / `cert-expired` /
  `domain-without-assignment`. SAN-aware (migration 0007 added `certificates.sans`
  JSON; agent populates from `openssl x509 -ext subjectAltName`) so apex+www
  cert pairs aren't flagged as separate misses. CLI `vsa fleet drift`,
  page UI `/health`.

- **Structural batch** (`c4ac973` + `86d8da2`) — 5 footguns from previous
  sessions: agent_audit_sync 500 (timestamp string→datetime), nginx
  healthcheck `(unhealthy)` (added default_server with /healthz, switched to
  127.0.0.1 to dodge IPv6/IPv4 split in alpine), promtail healthcheck
  `(unhealthy)` (dropped — `grafana/promtail:3.0.0` is FROM scratch),
  `PYTHONDONTWRITEBYTECODE=1` in vsa-agent.service (root agent stops
  dropping `__pycache__/*.pyc` into user-owned uv tool dirs), `bootstrap_vps.sh`
  now `chown`s `/var/log/vsa` + `/var/lib/vsa` to the calling user.

- Multiple agent fixes (`4765064` + `c9176da`): agent reads
  `cfg.mount_vhost_dir` (the bind-mount NGINX actually serves), not
  `cfg.repo_vhost_dir` (the git checkout — same on every VPS, misleading
  domain split); collect_domains skips `._*` AppleDouble files + UTF-8
  errors; multipoint regex `_ROUTE_RE` recognizes `set $route_1 X:port;`
  vhosts that the old `_UPSTREAM_RE` missed.

- **Doc alignment** (`db80452` + `f105cf3`) — CLAUDE.md / README /
  architecture / LLD / cursor rules / ADR-006 brought up to date with
  Phase A→E. Every CLI command's docstring gained an `Examples:` block
  so `vsa <cmd> --help` is now self-documenting; `vsa fleet drift --help`
  and `vsa cert health --help` also list the finding kinds inline.

- **`--help` callback bug** (`1818ffa`) — a `@app.callback()` on the
  `fleet` group was firing before Typer resolved `--help`, so reading
  the help required exporting `VSA_HUB_URL` first. Removed; the same
  validation already happens in `hub_client._client()` at the point of
  the first API call. Caught by another Claude session reviewing the
  CLI.

- **Fleet health timers** (`16a7ffb`) — two new systemd units in
  `infra/systemd/`: `vsa-drift.timer` (daily 08:00 → `vsa fleet drift`)
  and `vsa-fleet-cert-health.timer` (weekly Mon 09:00 →
  `vsa fleet cert-health --all`). Both `EnvironmentFile=/etc/vsa/agent.env`,
  output to journalctl, scraped by Promtail → Loki. Installed and active
  on the hub. Runbook at `docs/runbooks/fleet_health_timers.md`.

**Active configuration knobs to remember:**

- Hub-side: `VSA_HUB_URL=https://dashboard.flowbiz.ai/api` and
  `VSA_HUB_AUTH=admin:<pass>` in `/etc/vsa/agent.env` on vps-01.
- DNS-01 setup: per-VPS `cloudflare.ini` (mode 0600) at
  `/srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini`, enabled by
  `COMPOSE_FILE=compose.yml:compose.dns-cloudflare.yml` in
  `stacks/reverse-proxy/.env`. Active on **vps-02 + vps-03** (vps-02 added
  2026-06-01 — see top session). `vsa stack up` honors this `COMPOSE_FILE`
  override as of `fd0455f` (previously it silently dropped it).
- All three VPS run `vsa-agent.timer` (every 30s, oneshot, root) with
  `PYTHONDONTWRITEBYTECODE=1`.
- The hub also runs `vsa-drift.timer` (daily 08:00),
  `vsa-fleet-cert-health.timer` (weekly Mon 09:00), and **`vsa-alert.timer`
  (every 15 min)** which emails alarms on change (see the alerting section in
  the top session + `docs/runbooks/alerting.md`). Output → journalctl →
  Promtail → central Loki. **Active email alerts are now wired** via
  `vsa alert`; the drift/cert-health timers still only surface in
  `systemctl status` / journal.

**Active state of the registry (post-cleanup):**

| Domain | Primary | Standbys | Notes |
| --- | --- | --- | --- |
| `lokalflash.ch` (+`www`) | vps-02 | vps-03 | DNS-01 on standby; cleaned up rogue vhost on vps-01 |
| `app.lokalflash.ch` | vps-02 | vps-03 | DNS-01 on standby |
| `dev.lokalflash.ch` (+`www`) | vps-01 | — | HTTP-01 webroot, CF proxied for apex / DNS-only for www |
| 18 other flowbiz.ai/.com domains | vps-01 | — | auto-backfilled, all single-host |

`vsa fleet drift` returns `0 critical, 0 warning, 0 info` after the cleanup.

**Footguns rediscovered (don't repeat):**

1. macOS `tar` includes AppleDouble files (`._*`) with non-UTF-8 bytes that
   match `*.conf` glob → would crash `collect_domains` before the
   `c9176da` fix. Either avoid `tar` from a Mac for nginx confs, or just
   trust the defensive read in `collect_domains` post-fix.
2. `Base.metadata.create_all` at API boot creates *new tables* but **does
   not ALTER existing ones**. After adding a column (e.g. `certificates.sans`
   in migration 0007), do **not** `alembic stamp` the new revision —
   `alembic upgrade head` to actually run the ALTER. Hit this with 0007
   today; the fix is `alembic stamp <prev>; alembic upgrade head`.
3. CF Universal SSL on the free plan only covers the apex + one level of
   subdomain. Two-level subdomains (e.g. `www.dev.lokalflash.ch`) MUST
   be DNS-only (proxied=false), or you pay for Advanced Certificate Manager.
4. `lokalflash-{frontend,website,pocketbase}` containers on vps-01 are
   the **dev** environment, not prod leftovers. Don't `unprovision`
   without `--keep-container`.

**WIP not in repo yet:** none from this session — everything pushed to master.
A previous session left `wip/deployer-on-flowbiz1` on vps-01 only.

---

## Previous Session — 2026-05-04 (Multi-VPS-aware Dashboard)

> **Resume context.** A previous session deployed a coordinated change across the
> CLI, API, UI, and a new stack to make the dashboard reflect the **whole
> 3-VPS fleet** (`vps-01` hub + `vps-02` LokalFlash prod + `vps-03` warm standby),
> not just the host the API runs on. **Read this before touching any of:**
> `routers/{containers,domains,certs,stacks}.py`, `routers/agent.py`,
> `db/tables.py`, `services/agent_sync.py:collect_containers`,
> `stacks/observability/promtail-config.yml`, anything under `stacks/observability-agent/`.

**What landed (commits `dd540b7` + `12bf39c` + `ae28e1e`):**
- Read endpoints rewritten to query agent-synced PG tables (was
  `docker.from_env()` / disk scans → hub-only).
- New schema columns: `ContainerSnapshot.compose_{project,service}`,
  `Certificate.vps_id`. Migrations `0003` + `0004`. Composite
  `UNIQUE(vps_id, domain)` on `domains` + `certificates`.
- New stack `stacks/observability-agent/` (Promtail-only, ships logs to
  `loki.flowbiz.ai` with basic-auth + IP allow-list). Runs on vps-02 + vps-03.
- New nginx vhost `loki.flowbiz.ai.conf`. Let's Encrypt cert valid until 2026-08-02.
- Hub Promtail config now labels every stream with `vps_id: vps-01`.
- VSA agent **also** installed on the hub (it wasn't before — see footgun).
- `infra/scripts/setup_observability_agent.sh` orchestrates per-VPS install.
- See `docs/ADRs/005-multi-vps-aware-dashboard.md` for the architectural
  rationale and `docs/runbooks/observability_agent.md` for the per-VPS deploy
  procedure.

**Critical footguns hit during deploy (any future session WILL hit these):**
1. `vsa cert issue --domain X` adds `www.X` by default → fails LE for technical
   subdomains. **Always pass `--no-www` for `loki.*`, `dashboard.*`, `grafana.*`,
   `app.*`, `dev.*`, etc.**
2. `agent_audit_sync` returns 500 (DataError on the `timestamp` column —
   asyncpg expects datetime, gets ISO string). Pre-existing, **NOT FIXED**
   in this session. Containers/domains/certs sync are fine.
3. `dashboard-api` Dockerfile bug: shebangs of `.venv/bin/*` scripts point
   at the builder-stage path `/workspace/...` instead of `/app/...`.
   Run alembic via `docker exec ... /app/.venv/bin/python -m alembic upgrade head`,
   not `alembic` directly.
4. `observability-agent-promtail` shows `(unhealthy)` because the healthcheck
   uses `wget` which isn't in `grafana/promtail:3.0.0`. Functionally fine —
   logs are tailed and pushed. Replace `wget` with `nc -z localhost 9080` to fix.
5. `/var/log/vsa/` and `/var/lib/vsa/` are **root-owned** on a fresh VPS
   (created by the systemd service running as `root`). Interactive `vsa`
   commands by a non-root user fail with `OperationalError: unable to open
   database file`. Fix: `sudo chown -R <user>:<user> /var/log/vsa /var/lib/vsa`.
   Should be added to `bootstrap_vps.sh`.
6. The hub had **no** local VSA agent until this session — implicit assumption
   that "the hub is itself, no need to push". Wrong: the dashboard reads from
   the same table everyone pushes to. Fixed: hub now runs `vsa-agent.timer`
   (user `fgrosal`, `VSA_VPS_ID=vps-01`).

**Final dashboard state after the session:**

| Endpoint        | Total | Per VPS                                |
| --------------- | ----- | -------------------------------------- |
| `/containers`   | 44    | vps-01: 35, vps-02: 8, vps-03: 1       |
| `/domains`      | 39    | vps-01: 13, vps-02: 13, vps-03: 13     |
| `/certs`        | 26    | vps-01: 22, vps-02: 4, vps-03: 0       |
| `/stacks`       | 18    | vps-01: 14, vps-02: 3, vps-03: 1       |

Loki labels: `{vps_id="vps-01"}`, `{vps_id="vps-02"}`, `{vps_id="vps-03"}` all populated.

**WIP branch on flowbiz-1 only** (NOT pushed anywhere): `wip/deployer-on-flowbiz1`.
Contains the in-progress `apps/deployer/` + `stacks/deployer/` projects (a
GitHub-webhook-driven git-pull-and-redeploy mini-PaaS) plus 6 new vhosts
(`app.lokalflash.ch`, `lokalflash.ch`, `lokalflash.com`, `deploy.flowbiz.ai`,
`jobprospectai.flowbiz.ai`, `lopez.flowbiz.ai`) and improvements to
`routers/domains.py` (extracted `scan_active_domains()`,
`_parse_upstream()` helpers — superseded by this session's SQL-based rewrite,
the local helpers can be cherry-picked or dropped). The `deployer` container
keeps running through `git checkout` because its data lives in
`/srv/flowbiz/deployer/` (not in the repo).

---

## Project Overview

**FlowBiz VPS Admin Suite (VSA)** — A monorepo for managing multi-tenant hosting on Infomaniak VPS (primary) and Kamatera (legacy). Orchestrates Docker Compose stacks for AI apps (Dify, n8n, local LLMs) and customer websites behind an NGINX reverse proxy with Let's Encrypt SSL automation.

The project has three main components:
1. **`vsa` CLI** — Python/Typer CLI replacing all bash scripts with audited, tested tooling
2. **Dashboard** — FastAPI + Next.js centralized management at `dashboard.flowbiz.ai`
3. **Observability** — Grafana/Loki/Promtail/Prometheus with audit log pipeline and traffic analytics

## Build & Development Commands

```bash
# Stack lifecycle
make up                    # docker compose up -d --build
make down                  # docker compose down
make logs                  # docker compose logs -f --tail=200
make ps                    # docker compose ps

# Code quality
make lint                  # hadolint, ruff check, eslint
make format                # ruff format, prettier
make test                  # pytest (CLI unit tests)

# VSA CLI (primary tool for all infrastructure operations)
vsa site provision --domain X --container Y --port Z
vsa site provision --domain X --port Z --detect --external-port Z
vsa site unprovision --domain X [--keep-container] [--keep-cert] [-y]
vsa site list

# Multipoint provisioning (multiple backends on one domain)
vsa site provision --domain promoflash.flowbiz.ai \
  --route /=promoflash-frontend:80 \
  --route /api/=promoflash-pocketbase:8090 \
  --route /_/=promoflash-pocketbase:8090
vsa auth add --domain X --user Y
vsa auth remove --domain X
vsa cert renew
vsa cert status
vsa cert install-cron
vsa vhost sync
vsa stack new NAME
vsa stack up NAME
vsa bootstrap

# VPS fleet management (registry)
vsa vps list
vsa vps add --id vps-02 --hostname myserver --ip 1.2.3.4
vsa vps remove VPS_ID [-y]

# Multi-VPS write-side ops (run on the hub; needs VSA_HUB_URL + VSA_HUB_AUTH)
vsa fleet assign --domain X --primary vps-Y --standbys vps-Z[,vps-W]
vsa fleet list / show DOMAIN / remove DOMAIN
vsa fleet backfill [--dry-run]
vsa fleet exec --vps vps-X --timeout 120 -- <argv>
vsa fleet vhost-sync --vps vps-X
vsa fleet cert-renew --vps vps-X
vsa fleet cert-health [--vps vps-X | --all]
vsa fleet site-provision --domain X --primary vps-Y --standbys vps-Z \
                         --container c --port p [--no-www]
vsa fleet drift [--show-info]

# Cert health + DNS-01 issuance
vsa cert health
vsa cert issue --domain X --challenge dns-cloudflare
vsa cert issue --domain X --no-www  # technical subdomains
vsa site provision --domain X --container c --port p --standby  # warm-standby

# Make targets delegate to CLI
make provision-container domain=<domain> port=<port> [nowww=true]
make unprovision-container domain=<domain>
make sync-vhosts
make check-certs
make add-basic-auth domain=<domain> user=<user> password=<password>
make remove-basic-auth domain=<domain>
```

Per-stack Makefiles exist in `stacks/reverse-proxy/`, `stacks/dashboard/`, etc.

## Architecture

See `docs/architecture.md` for full architecture documentation with diagrams.

### Monorepo Layout

- **`packages/python/vsa-common/`** — Shared Pydantic models and constants (AuditEvent, SiteConfig, VsaConfig)
- **`apps/`** — Application code
  - `vps-admin-cli/` — `vsa` CLI (Typer + Jinja2 + bcrypt + audit logging)
  - `vps-admin-api/` — Dashboard API (FastAPI + SQLAlchemy + PostgreSQL + Loki)
  - `vps-admin-ui/` — Dashboard frontend (Next.js 14 + Tailwind + React Query)
- **`stacks/`** — Docker Compose stacks, each self-contained with `compose.yml`, `.env.example`, and `README.md`
  - `reverse-proxy/` — NGINX 1.25 + Certbot (core infrastructure)
  - `dashboard/` — Dashboard stack (API + UI + PostgreSQL)
  - `dify/` — LLM platform
  - `observability/` — Grafana 10.4, Loki 3.0, Promtail 3.0, Prometheus 2.53, Node Exporter, cAdvisor
  - `llm-gateway/` — Placeholder for LLM backend routing
  - `templates/` — Reusable compose snippets
- **`infra/`** — Infrastructure automation
  - `scripts/` — Legacy bash scripts (being superseded by CLI)
  - `scripts/_deprecated/` — One-off migration scripts
  - `systemd/` — systemd units for VSA agent
- **`docs/`** — Architecture docs, runbooks, and ADRs

### Dashboard API

11 routers: `agent`, `assignments`, `audit_logs`, `certs`, `containers`, `domains`, `fleet`, `stacks`, `traffic`, `vps`

The `assignments` router exposes the user-edited intent registry (primary +
standbys per domain). The `fleet` router exposes the read-only
`/api/fleet/drift` cross-check between intent and observed state. The
`agent` router gained an exec-channel block (POST `/agent/exec`, GET/POST
`/agent/commands[/{id}/{take,result}]`) for hub→agent command execution.

Key services:
- **Loki client** (`services/loki.py`) — queries Loki for raw traffic logs and aggregated stats via LogQL metric queries (`count_over_time`, `sum_over_time`, `avg_over_time` with `unwrap`)
- **Certificate scanner** (`routers/certs.py`) — reads Let's Encrypt cert files from disk via `cryptography` library, returns live expiry dates and status
- **Audit logs** (`routers/audit_logs.py`) — reads from local SQLite (`/var/lib/vsa/audit.db`, mounted in container) for hub events, merges with PostgreSQL for remote agent events, deduplicates by (timestamp, actor, action, target)

### Dashboard UI

9 pages: Overview (`/`), **Fleet Health** (`/health`), Containers, Domains, Certificates, **Assignments** (`/assignments`), Traffic, Audit, VPS

### Traffic Analytics Pipeline

```
NGINX (json_detailed log format per domain)
  → Promtail (scrapes *.access.json, extracts domain/method/status labels)
    → Loki (stores with job="nginx-domain-access")
      → Dashboard API (LogQL metric queries for aggregated stats)
        → Dashboard UI (stats cards, per-domain breakdown, raw logs table)
```

The traffic stats endpoint queries Loki directly using LogQL aggregation — no dependency on the agent sync or PostgreSQL for traffic data.

### Certificate Monitoring

The `/api/certs` endpoint reads Let's Encrypt certificates directly from disk (`/etc/letsencrypt/live/*/fullchain.pem`) using the `cryptography` library. Returns live expiry dates, days remaining, and status (`valid`, `warning` <=30d, `critical` <=14d, `expired`).

### VSA CLI Architecture

```
apps/vps-admin-cli/src/vsa/
├── cli.py              # Root Typer app
├── config.py           # VsaConfig singleton (paths from VSA_ROOT env)
├── audit.py            # Dual-write JSONL + SQLite audit logger
├── errors.py           # Custom exceptions
├── commands/           # CLI command groups (site, cert, auth, stack, vhost, vps, bootstrap, agent)
├── services/           # Business logic (docker, nginx, certbot, htpasswd, vhost_renderer, network, agent_sync)
├── templates/          # Jinja2 NGINX vhost templates
└── models/             # Re-exported Pydantic models
```

Key design decisions:
- **Jinja2 templates** for vhost generation (replaces sed placeholder substitution)
- **bcrypt** for htpasswd entries (replaces APR1/MD5)
- **`nginx -t` before reload** (validates config before applying)
- **Structured audit logging** to `/var/log/vsa/audit.jsonl` + `/var/lib/vsa/audit.db`
- **Pydantic config** with `VSA_ROOT` env var (no hardcoded paths)

### Dashboard Docker Build

The API Dockerfile uses the **repo root** as its build context (set in `stacks/dashboard/compose.yml`) so it can access `packages/python/vsa-common/`. The Dockerfile mirrors the repo layout at `/workspace/` to keep `pyproject.toml` relative paths working. A root `.dockerignore` excludes `.git`, `node_modules`, `stacks/`, etc. to keep the build context small. The runtime stage uses `PYTHONPATH=/app/src` since `uv sync` creates editable installs with builder-stage paths.

### Networking Model

All stacks join the shared `flowbiz_ext` Docker network for reverse proxy access. Each stack also has its own internal network (e.g., `dashboard-net`) for inter-service communication. No database ports are exposed publicly.

### Storage Strategy

**Two-disk layout** (see `/etc/fstab`):
- **Root `/`** — OS, configs, bind-mounted config files (vhosts, certs, `.env` files)
- **`/var/lib/docker` (`/dev/sdb`)** — Docker named volumes (observability data, app data)

**Observability data** uses Docker named volumes (stored on `/dev/sdb`):
- `obs-prometheus-data` — Prometheus TSDB (capped at 1GB / 15 days)
- `obs-loki-data` — Loki chunks and indexes (30-day retention)
- `obs-grafana-data` — Grafana dashboards and state
- `obs-promtail-data` — Promtail position tracking

**Bind mounts on root `/`** (config and small data only):
- `/srv/flowbiz/reverse-proxy/` — NGINX vhosts, certs, auth files, access logs
- `/srv/flowbiz/dashboard/data/` — PostgreSQL data
- `/srv/flowbiz/observability/data/grafana-provisioning/` — Grafana provisioning configs

### Data Path Convention

```
/srv/<tenant>/<app>/
├── data/    # Application data, DB volumes
├── env/     # .env files (chmod 640, never committed)
├── logs/    # Application logs
└── compose/ # Optional compose overrides
```

### Reverse Proxy & TLS

NGINX vhost files live in `stacks/reverse-proxy/nginx/conf.d/<domain>.conf`. Every vhost includes `security_headers.conf` (HSTS, X-Frame-Options DENY, CSP) and rate limiting. Per-domain JSON access logs are written to `/var/log/nginx/domains/<domain>.access.json` using the `json_detailed` log format. Certbot handles Let's Encrypt via HTTP-01 (webroot). The `vsa site provision` command handles the full workflow: network attach, HTTP vhost, cert issuance, HTTPS vhost, nginx reload.

### Certificate Auto-Renewal

The reverse-proxy stack includes three services for automated TLS:
- **`reverse-proxy-certbot`** — runs `certbot renew` every 12 hours, automatically renews any cert within 30 days of expiry
- **`reverse-proxy-reloader`** — reloads NGINX every 6 hours via `docker exec` to pick up renewed certs (uses `docker:27-cli` image with Docker socket access)
- **`reverse-proxy-nginx`** — serves traffic, mounts `/etc/letsencrypt` for cert access

All renewal configs live in `/srv/flowbiz/reverse-proxy/letsencrypt/renewal/<domain>.conf`. Each domain provisioned via `vsa site provision` automatically gets a renewal config.

### Site Unprovision

`vsa site unprovision --domain X` performs comprehensive cleanup (6 steps):
1. Remove vhost config (repo + mount directories)
2. Remove auth/htpasswd files
3. Delete Let's Encrypt certificate (via certbot)
4. Remove per-domain access log files
5. Stop and remove container (with shared container detection)
6. Reload NGINX

**Shared container detection:** Before removing a container, scans all vhost configs to find other domains pointing to the same upstream container. If shared, warns the user and asks whether to keep or remove the container. Flags: `--keep-container`, `--keep-cert`, `--yes/-y` for non-interactive mode.

### Multi-VPS Strategy (Hub-and-Agent)

**Every VPS in the fleet — including the hub itself — runs `vsa agent`.** The hub
ran the dashboard but had no local agent until the 2026-05-04 session, which
left vps-01 underrepresented in the dashboard tables. Don't repeat that mistake.

- `vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token XXX`
- `vsa agent start` (via systemd timer, every 30s)
- Sends heartbeats, container snapshots, cert status, traffic stats, and audit events to hub

**VPS fleet management** via `vsa vps`:
- `vsa vps list` — list all registered VPS nodes (table with ID, hostname, IP, status, last seen)
- `vsa vps add --id vps-02 --hostname X --ip Y` — pre-register a VPS node in the dashboard
- `vsa vps remove VPS_ID [-y]` — remove a VPS and all associated data (domains, snapshots, traffic)

**Adding a new VPS to the system:**
1. On the hub: `vsa vps add --id vps-02 --hostname newserver --ip 1.2.3.4`
2. On the new VPS: install CLI, then `vsa agent register --hub-url ... --token ...`
3. On the new VPS: ensure `/var/log/vsa/` and `/var/lib/vsa/` exist and are
   `chown`-ed to the user that runs the systemd unit (or to root + run unit as
   root). The agent will silently fail with `OperationalError: unable to open
   database file` if these are missing or unwritable.
4. On the new VPS: enable the systemd timer — it takes over and syncs every 30s.
5. (Optional but recommended) Deploy the `observability-agent` stack so the
   new VPS ships its container/nginx/audit logs to the central Loki — see
   `docs/runbooks/observability_agent.md`.

### Multi-VPS Dashboard Read Path (Tier 1+2, since 2026-05-04)

The dashboard read endpoints (`/containers`, `/domains`, `/certs`, `/stacks`)
**no longer scan the local Docker socket / nginx config dir / Let's Encrypt
store**. They query the agent-synced PostgreSQL tables (`container_snapshots`,
`domains`, `certificates`) populated by the existing `/agent/*-sync` endpoints.
Every response carries `vps_id`. Freshness is bounded by the agent tick (~30s).

Schema invariants worth knowing:
- `container_snapshots` has `compose_project` and `compose_service` columns
  (extracted by `collect_containers` from the `Labels` field returned by
  `docker ps --format '{{json .}}'`).
- `domains` and `certificates` use composite `UNIQUE(vps_id, domain)`, NOT
  `UNIQUE(domain)`. This lets the same cert/vhost coexist on the primary VPS
  and a warm-standby VPS — important for the LokalFlash flowbiz-2/flowbiz-3
  active/standby pair.
- `agent_certs_sync` and `agent_domains_sync` perform stale-removal scoped by
  `vps_id`. Don't reintroduce an unscoped `WHERE domain NOT IN (...)` — it
  would let one agent wipe another VPS's records.

### Multi-VPS Log Shipping (Tier 3, since 2026-05-04)

Remote VPS (everything except the hub) run the lightweight
`stacks/observability-agent/` (Promtail-only, ~50 MB RAM). They push logs
to the hub's Loki over **HTTPS + basic auth + IP allow-list**:

- Vhost: `loki.flowbiz.ai` proxies to `observability-loki-1:3100` on the hub.
- Auth: `auth_basic` against `/etc/nginx/auth/loki.flowbiz.ai.htpasswd`,
  defence-in-depth on top of the IP allow-list (which lists each VPS public
  IP and `deny all` otherwise).
- Each Promtail emits labels: static `vps_id`, plus `container`,
  `compose_project`, `compose_service`, `stream` from `docker_sd_configs`,
  plus `domain`, `method`, `status` extracted from JSON nginx logs.

**The hub** runs the older `stacks/observability/` (Loki + Grafana +
Prometheus + Promtail). The hub Promtail also has `vps_id: vps-01` on every
job, so its streams are filterable too.

**To rotate the Loki basic-auth password:** regenerate the bcrypt hash
in `/srv/flowbiz/reverse-proxy/nginx/auth/loki.flowbiz.ai.htpasswd` on the hub,
then update `LOKI_BASIC_AUTH_PASSWORD` in `/srv/flowbiz/observability-agent/env/.env`
on every agent VPS, then `vsa stack up observability-agent` on each.

**To add a new VPS to the allow-list:** edit `stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf`,
add the new `allow X.X.X.X;` line, push, pull on the hub, copy to
`/srv/flowbiz/reverse-proxy/nginx/conf.d/`, `docker exec reverse-proxy-nginx nginx -s reload`.

### Agent Sync Reconciliation

Agent sync endpoints perform **full reconciliation**, not append-only:
- **`domains-sync`** — upserts domains from the payload, then deletes DB entries for that VPS that are no longer in the vhost directory (stale domains removed automatically after unprovision)
- **`certs-sync`** — same pattern: upserts current certs, deletes stale entries no longer reported
- **`containers-sync`** — full replacement: deletes all snapshots for the VPS, re-inserts current state
- **`DELETE /api/agent/vps/{vps_id}`** — removes a VPS node and all associated data (domains, snapshots, traffic stats)

## Conventions

- **Language defaults:** Python 3.11+, Node 20 LTS
- **Python tooling:** `uv` (preferred), Ruff for lint/format, pytest, FastAPI + Uvicorn, Typer for CLIs, Pydantic for config
- **Node tooling:** pnpm, ESLint, Prettier, Next.js + Tailwind
- **Docker:** Multi-stage builds, non-root users, slim base images, `HEALTHCHECK` required, images < 300MB, `restart: unless-stopped` on **every** container (verified reboot-safe)
- **Git:** Trunk-based development, conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.), SemVer tags
- **Bash scripts:** Use `set -Eeuo pipefail`, idempotent, clear logging with `echo "[step] ..."`
- **Indentation:** 2 spaces default, 4 spaces for Python, tabs for Makefiles (see `.editorconfig`)
- **Audit logging:** Every infrastructure operation must use the `audit()` context manager

## When Generating New Stacks

1. Create `stacks/<name>/compose.yml`, `.env.example`, `README.md`, and optionally `Makefile`
2. Include healthchecks, restart policies, volumes, and network configuration
3. Networks: join `flowbiz_ext` + create local `<name>-net`
4. Use `vsa stack new NAME` to scaffold from template
5. Provision with `vsa site provision --domain X --container Y --port Z`

## When Adding CLI Commands

1. Create command module in `apps/vps-admin-cli/src/vsa/commands/`
2. Register in `cli.py` via `app.add_typer()` or `app.command()`
3. Wrap operations with `audit()` context manager
4. Add unit tests in `apps/vps-admin-cli/tests/`
5. Run tests: `cd apps/vps-admin-cli && uv run pytest -q`

## When Adding API Endpoints

1. Create or extend a router in `apps/vps-admin-api/src/vsa_api/routers/`
2. Register in `main.py` via `app.include_router(router, prefix="/api")`
3. For live data, prefer reading from disk or Loki over PostgreSQL when possible (avoids stale data from agent sync)
4. Add TypeScript types in `apps/vps-admin-ui/src/lib/api.ts`
5. Rebuild: `cd stacks/dashboard && docker compose up -d --build dashboard-api`

## Keeping Rules in Sync

After any significant change (new stacks, architectural shifts, new tooling, changed conventions), update **all of these**:
- **`CLAUDE.md`** (this file) — for Claude Code
- **`.cursor/rules/my-custom-rules.mdc`** — for Cursor IDE
- **`docs/architecture.md`** — high-level architecture for human reference
- **`docs/low-level-design.md`** — storage, retention, networking, container internals
- **`README.md`** — project overview and quick start

This ensures all AI assistants and developers stay aligned with the current state of the project.

## Implementation Status

All 7 phases of the VSA modernization plan have been implemented and committed, plus additional traffic analytics and certificate monitoring features.

### Completed
- **Phase 1 — Foundation:** Shared library (`packages/python/vsa-common/`), CLI skeleton, Jinja2 vhost renderer, bcrypt htpasswd service, 30 unit tests
- **Phase 2 — CLI Core:** All command modules (site, cert, auth, stack, vhost, vps, bootstrap, agent) with audit logging
- **Phase 3 — Observability:** Loki 30-day retention, Promtail audit scrape pipeline, NGINX rate limiting zones
- **Phase 4 — Dashboard Backend:** FastAPI + SQLAlchemy async + PostgreSQL, 9 API routers, Alembic migrations (6 tables), Docker SDK integration, multi-stage Dockerfile (repo root build context with `PYTHONPATH`)
- **Phase 5 — Dashboard Frontend:** Next.js 14 + Tailwind + React Query, 7 pages (overview, containers, domains, certs, audit, traffic, VPS), sidebar nav, status badges, standalone output mode
- **Phase 6 — Multi-VPS Agent:** systemd service + timer units, agent register/start/status commands, traffic stats sync
- **Phase 7 — Cleanup:** Deprecated one-off scripts, updated Makefile to delegate to CLI, 4 ADRs, updated runbooks, consolidated root README
- **Phase 8 — Traffic Analytics:** Per-domain NGINX JSON logging, Promtail `nginx-domain-access` scrape job, Loki LogQL metric queries for aggregated stats, traffic dashboard page with stats cards, per-domain breakdown, raw logs table
- **Phase 9 — Live Certificate Monitoring:** `cryptography` library for parsing cert files from disk, live expiry dates/status/days remaining, color-coded status badges (valid/warning/critical/expired)
- **Phase 10 — Certificate Auto-Renewal:** Certbot container runs `certbot renew` every 12h, nginx-reloader sidecar reloads NGINX every 6h via Docker socket, replaces broken `deploy-hook` approach
- **Phase 11 — Comprehensive Unprovision:** `vsa site unprovision` performs 6-step cleanup (vhost, auth, cert, logs, container, nginx reload) with shared container detection and interactive prompts
- **Phase 12 — Reboot Resilience:** All containers have `restart: unless-stopped`, Docker daemon enabled on boot, verified all 36 containers survive unattended reboot
- **Phase 13 — Sync Reconciliation & VPS Fleet Management:** Agent sync now performs full reconciliation (stale domains/certs auto-removed after unprovision), audit logs endpoint reads directly from local SQLite (no agent sync dependency for hub events), new `vsa vps` CLI command group (list/add/remove), `DELETE /api/agent/vps/{vps_id}` endpoint
- **Phase 14 — Storage Architecture:** Observability data moved from bind mounts (`/srv/flowbiz/`) to Docker named volumes (`/var/lib/docker/volumes/` on dedicated disk). Prometheus retention set to 15d/1GB cap. Loki retention reduced from 90d to 30d. Containers run as image-default users (no `user: "1001"` override).

### Deployed
- **Dashboard live** at `https://dashboard.flowbiz.ai/` — API + UI + PostgreSQL running on VPS-01, TLS via Let's Encrypt (auto-renew), HTTP Basic Auth (`admin`), NGINX reverse proxy routing `/api/*` to API and `/*` to UI
- **Traffic analytics live** — all domains showing real-time traffic stats from Loki
- **Certificate monitoring live** — real expiry dates read from Let's Encrypt cert files on disk
- **CLI installed** on VPS-01 at `~/.local/bin/vsa`
- **Frontend dependencies** installed (pnpm lockfile generated)
- **Alembic migrations:** `0001_initial_tables.py` (5 tables), `0002_traffic_stats.py` (traffic_stats table)

### Pending / Known Issues
- **GitHub Actions CI** (`.github/workflows/ci.yml`) — file exists locally but was removed from git because the GitHub PAT lacks the `workflow` scope. To re-add: update PAT with `workflow` scope, then `git add .github/workflows/ci.yml && git commit -m "ci: add GitHub Actions pipeline" && git push`
- **No `.env` files committed** — by design; `.env.example` files provide templates
- **NGINX healthcheck** — `reverse-proxy-nginx` shows `unhealthy` because the healthcheck calls `http://localhost/healthz` but there's no default server block for `localhost`; doesn't affect functionality

### Test Status
- 30+ CLI unit tests passing: `cd apps/vps-admin-cli && uv run pytest -q`
- Tests cover: Pydantic models, VsaConfig, bcrypt htpasswd, Jinja2 vhost rendering, audit logging (JSONL + SQLite), agent sync traffic collection
- `uv` must be on PATH: installed at `~/.local/bin/uv` via `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Quality Gates

Do not merge if:
- Missing `.env.example` (with placeholder-only values)
- No healthcheck in Dockerfile/compose
- No README for a new stack
- DB ports exposed to the internet
- NGINX vhost missing security headers/HSTS
- CLI command missing audit logging
- Tests failing (`cd apps/vps-admin-cli && uv run pytest -q`)
