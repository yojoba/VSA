# ADR-005: Multi-VPS-Aware Dashboard Read Path + Log Shipping

## Status
Accepted (deployed 2026-05-04)

## Context

ADR-004 established the hub-and-agent model: the `vsa agent` running on each
VPS pushes container snapshots, cert status, domains, traffic stats, and
audit events to the hub via `/api/agent/*-sync` endpoints. The hub stores
everything in PostgreSQL.

But the **dashboard read endpoints** never used those tables. They scanned
the local filesystem of the host the API was running on:
- `/api/containers` called `docker.from_env()` → only listed the hub's containers
- `/api/domains` walked `/etc/nginx/conf.d/*.conf` → only the hub's vhosts
- `/api/certs` walked `/etc/letsencrypt/live/*` → only the hub's certs
- `/api/stacks` re-aggregated `docker.from_env()` → only the hub's stacks

The dashboard at `https://dashboard.flowbiz.ai` therefore **lied about the
fleet**: it claimed everything was hub-local even though `/agent/*-sync` was
ingesting data from vps-02 and vps-03 into PostgreSQL. The discrepancy was
tolerable when the fleet was a single host; once vps-02 (LokalFlash prod)
and vps-03 (warm standby) joined, it became actively misleading — a partner
debugging "is my container running on vps-02?" got an answer about vps-01.

In parallel, the **traffic page** depended on Loki, but only the hub's
Promtail was scraping anything. Logs from containers running on vps-02 and
vps-03 were not visible anywhere centrally — `docker logs` over SSH was the
only option.

Two `agent_*_sync` bugs amplified the damage:
1. `agent_certs_sync` did stale-removal **without scoping by `vps_id`** —
   when vps-02 synced its certs, it deleted vps-01's certs from the table
   because they weren't in vps-02's payload. The hub had `Certificate.domain
   UNIQUE` (no `vps_id` column at all), so two VPS hosting the same cert
   (the warm-standby pattern) couldn't coexist in the table either.
2. `agent_domains_sync` upserted by `(domain)` only, not `(domain, vps_id)`,
   so the same vhost on two VPS would get its `vps_id` overwritten on every
   alternate sync.

## Decision

Three coordinated changes across CLI, API, UI, and a new stack:

### Tier 1+2 — Read endpoints query the agent-synced tables

`routers/{containers,domains,certs,stacks}.py` rewritten to read from
PostgreSQL (`container_snapshots`, `domains`, `certificates`) instead of
local filesystem/Docker. Each response carries `vps_id`. The UI gets a
dedicated VPS column.

The `/api/stacks` endpoint groups `container_snapshots` by
`(vps_id, compose_project)` so the same compose stack name (`reverse-proxy`)
running independently on multiple VPS appears as distinct entries.

Schema migrations (`0003`, `0004`):
- `ContainerSnapshot.compose_project` (already added in `0003`) +
  `ContainerSnapshot.compose_service` (added in `0004`).
- `Certificate.vps_id` (new, default `vps-01` for backfill).
- `domains.UNIQUE(domain)` → `UNIQUE(vps_id, domain)`.
- `certificates.UNIQUE(domain)` → `UNIQUE(vps_id, domain)`.

`agent_certs_sync` and `agent_domains_sync` patched:
- Stale-removal scoped by `vps_id`.
- Upsert lookup uses composite `(domain, vps_id)`.
- Cert insert now writes `vps_id`.

`collect_containers` (in `services/agent_sync.py`) extracts both
`com.docker.compose.project` and `com.docker.compose.service` from the
`Labels` field returned by `docker ps --format '{{json .}}'` (the field is
a comma-separated string `key1=val1,key2=val2,...`).

### Tier 3 — New `observability-agent` stack for log shipping

Remote VPS run `stacks/observability-agent/` (Promtail-only, ~50 MB RAM).
It scrapes:
- All Docker containers via `docker_sd_configs` (auto-discovery via the
  Docker socket; tags `container`, `compose_project`, `compose_service`,
  `stream`).
- nginx per-domain JSON access logs from `/srv/flowbiz/reverse-proxy/logs/domains/*.access.json`
  (parsed with the JSON pipeline stage, extracts `domain`, `method`, `status`).
- nginx error logs from `/srv/flowbiz/reverse-proxy/logs/error*.log`.
- VSA CLI audit trail at `/var/log/vsa/audit.jsonl`.
- systemd journal.

Every stream is labelled `vps_id: ${VSA_VPS_ID}` so the dashboard `/traffic`
page and any Grafana panel can filter per VPS.

Logs ship to the hub's Loki over `https://loki.flowbiz.ai/loki/api/v1/push`
with **HTTP basic auth + IP allow-list** (defence in depth). The vhost lives
at `stacks/reverse-proxy/nginx/conf.d/loki.flowbiz.ai.conf`. The Loki port
3100 is *not* exposed publicly.

Promtail expands `${VSA_VPS_ID}`, `${LOKI_URL}`, `${LOKI_BASIC_AUTH_USER}`,
`${LOKI_BASIC_AUTH_PASSWORD}` from the env at boot via `-config.expand-env=true`.

The hub's existing `stacks/observability/` Promtail also got `vps_id: vps-01`
labels, so its streams are filterable too. The hub also runs the standard
`vsa-agent.timer` now (it was missing — see footgun #6).

### Why basic-auth + IP allow-list, not Tailscale or WireGuard

- Tailscale was considered (free for personal use, simpler than running
  your own mesh) but the user didn't want a SaaS dependency.
- WireGuard maison would have been ~2-3 h of plumbing for a single tunnel
  — over-engineering for 3 VPS.
- nginx + auth_basic + IP allow-list reuses the existing reverse-proxy +
  Let's Encrypt automation already battle-tested for every other vhost.
  IPs are static (Infomaniak Public Cloud), so the allow-list is one-time
  config per new VPS.

## Consequences

### What works now

- The dashboard reflects all 3 VPS at `https://dashboard.flowbiz.ai`:
  containers (44), domains (39), certs (26), stacks (18) all show the right
  `vps_id`.
- Grafana queries like `{vps_id="vps-02"} |= ""` show LokalFlash prod logs in
  near-real-time.
- Warm-standby pre-positioning is honest: a vhost or cert deployed on both
  vps-02 and vps-03 appears as 2 rows (composite unique key) instead of one
  ping-pong-overwritten row.

### Trade-offs accepted

- **Freshness ~30s** for read endpoints (was real-time, since they scanned
  Docker locally). Net upgrade since the alternative was hub-only data.
- **Static IP allow-list** means adding a new VPS requires editing
  `loki.flowbiz.ai.conf` + nginx reload. Acceptable given Infomaniak Public
  Cloud IPs are stable.
- **Loki retention** still 30 days; cardinality budget bounded by
  per-container labels. Watch disk if a tenant container goes verbose
  (n8n, dify can produce GB/day).

### What we left unfixed (intentionally — out of scope)

- `agent_audit_sync` returns 500 with `DataError: ... expected datetime,
  got str`. Pre-existing, not in this session's blast radius. Containers/
  domains/certs sync is unaffected.
- `dashboard-api` Dockerfile copies `.venv` from the builder stage but the
  shebangs of `.venv/bin/*` scripts still point at the builder path
  `/workspace/...`. Run console scripts via `python -m alembic` instead of
  `alembic` directly.
- `observability-agent-promtail` healthcheck uses `wget` which isn't in
  the `grafana/promtail:3.0.0` image. Container reports `unhealthy` but
  functionally everything works. Replace with `nc -z localhost 9080`.
- `bootstrap_vps.sh` should `chown -R $TARGET_USER /var/log/vsa /var/lib/vsa`
  so interactive `vsa` commands by the non-root user don't fail with
  `OperationalError: unable to open database file`.

### Operational notes

- See `docs/runbooks/observability_agent.md` for the per-VPS deploy procedure.
- See `CLAUDE.md` "Latest Session" footgun list for everything that bit
  during this rollout — `vsa cert issue --no-www` for technical subdomains
  is the most likely repeat.
