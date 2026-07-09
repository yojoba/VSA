# Fleet Alerting Runbook

Email alarms for **certificate** and **system** problems across the fleet,
driven by `vsa alert` + a systemd timer on the hub.

## What it watches

`vsa alert check` queries the hub API and raises a problem for any of:

| Category | Source | Levels |
| --- | --- | --- |
| `cert` | `/fleet/drift` (kinds containing `cert`) | expiring-soon → warning/critical, expired/missing → critical |
| `drift` | `/fleet/drift` (other kinds: rogue-host, missing-on-primary/standby, …) | as reported |
| `agent` | `/vps` — `last_seen` age | stale > `AGENT_STALE_MINUTES` → critical; never reported → warning |
| `container` | `/containers` — docker status | down (Exited≠0 / Dead / Restarting) → critical; `(unhealthy)` → warning |
| `disk` | Prometheus `node_filesystem_*` (all VPS, per watched mountpoint) | ≥ `DISK_WARN_PERCENT` → warning; ≥ `DISK_CRIT_PERCENT` → critical |
| `endpoint` | Prometheus blackbox `probe_success` (external synthetic probe, `vps=ext`) | down for ≥3 min → critical |
| `cert` (external) | Prometheus blackbox `probe_ssl_earliest_cert_expiry` (`vps=ext`) | < `CERT_WARN_DAYS` → warning; < `CERT_CRIT_DAYS` → critical |

Only problems at or above `VSA_ALERT_MIN_LEVEL` (default `warning`) are kept.

> The `disk` check reads the hub's Prometheus (`VSA_ALERT_PROMETHEUS_URL`,
> default `http://localhost:9090`), which holds host filesystem metrics for
> **every** VPS via the observability-agent remote-write. It watches the
> mountpoints in `VSA_ALERT_DISK_MOUNTS` (default `/|/var/lib/docker`). If
> Prometheus is unreachable the check is skipped (a down Prometheus is already
> caught by the `container` check).

> The `endpoint` + external-`cert` checks read the **blackbox** job in the same
> Prometheus (see `docs/low-level-design.md` → Blackbox Exporter). They probe the
> public LokalFlash K8s app (`app.lokalflash.ch`, `www.lokalflash.ch`) from the
> hub — an off-cluster vantage that catches edge outages (ingress/DNS/cert) the
> app's in-cluster Sentry can't see. `endpoint` uses a 3-min `max_over_time`
> window (debounces flaky probes); the external `cert` thresholds are
> `VSA_ALERT_CERT_WARN_DAYS` (default 14) / `VSA_ALERT_CERT_CRIT_DAYS` (default 3)
> — a backstop for cert-manager silently failing to renew. Same Prometheus-down
> behaviour: skipped, not fatal.

## Anti-spam: alert on change, not every run

The check writes the set of currently-firing problems to
`/var/lib/vsa/alert-state.json`. On each run it emails **only when the set
changes**:

- a **new or escalated** problem appears → 🔴 alert listing all active problems (new ones flagged `NOUVEAU`);
- **all** problems clear → ✅ recovery email;
- nothing changed → silent.

`vsa alert check --force` emails the current state regardless.

## Commands

```bash
vsa alert status            # show current problems, no email
vsa alert check             # the timer's job: email on change
vsa alert check --dry-run   # preview the email, send nothing
vsa alert check --force     # email current state now
vsa alert test              # send a test email to verify SMTP + recipients
```

`vsa alert check` exits 1 when a critical problem is active (handy for CI);
the systemd unit treats that as success (`SuccessExitStatus=0 1`).

## Install (on the hub, vps-01)

```bash
# 1. Config — SMTP creds + recipients. NEVER commit this file.
sudo install -m 600 -o root -g root \
  ~/dev/github/VSA/infra/systemd/alert.env.example /etc/vsa/alert.env
sudo nano /etc/vsa/alert.env            # set VSA_ALERT_SMTP_PASSWORD etc.

# 2. Sanity check
set -a; sudo cat /etc/vsa/agent.env /etc/vsa/alert.env > /tmp/e.env; source /tmp/e.env; rm /tmp/e.env; set +a
vsa alert test                          # expect a ✅ test email
vsa alert status                        # see current problems

# 3. Timer (every 15 min)
sudo cp ~/dev/github/VSA/infra/systemd/vsa-alert.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vsa-alert.timer
systemctl list-timers vsa-alert.timer
```

## Turn it on / off / pause

```bash
# ON  (enable + start firing every 15 min)
sudo systemctl enable --now vsa-alert.timer

# OFF (stop + don't start on boot)
sudo systemctl disable --now vsa-alert.timer

# PAUSE temporarily (until next reboot or manual start)
sudo systemctl stop vsa-alert.timer

# Is it on? next fire?
systemctl is-enabled vsa-alert.timer
systemctl list-timers vsa-alert.timer
```

Disabling the timer stops all emails immediately; the `vsa alert` commands
still work by hand. To keep the timer running but **mute a specific noisy
problem**, prefer raising the level or the ignore-list below rather than
disabling everything.

### Quick on/off knobs (edit `/etc/vsa/alert.env`, no restart needed)

| Want to… | Change |
| --- | --- |
| Only get **critical** alerts (quieter) | `VSA_ALERT_MIN_LEVEL=critical` |
| Get **everything** incl. info | `VSA_ALERT_MIN_LEVEL=info` |
| Change who's emailed | `VSA_ALERT_TO=a@x.ch,b@y.ch` |
| Stop alerting on a cosmetic-unhealthy container | add its name to `VSA_ALERT_IGNORE_CONTAINERS` (comma substrings) |
| Be more/less patient about a silent agent | `VSA_ALERT_AGENT_STALE_MINUTES=30` |
| Tune disk alarm thresholds | `VSA_ALERT_DISK_WARN_PERCENT=85`, `VSA_ALERT_DISK_CRIT_PERCENT=92` |
| Watch more/other mountpoints | `VSA_ALERT_DISK_MOUNTS=/|/var/lib/docker|/data` (PromQL regex) |

Each `vsa alert` run reads the env fresh, so edits take effect on the next
timer fire (≤15 min) — no `systemctl restart` required.

## Configuration reference

All `VSA_ALERT_*` env vars live in `/etc/vsa/alert.env`. See
`infra/systemd/alert.env.example` for the full annotated list. The most useful:

- `VSA_ALERT_TO` — comma-separated recipients.
- `VSA_ALERT_MIN_LEVEL` — `info` | `warning` | `critical`.
- `VSA_ALERT_AGENT_STALE_MINUTES` — how long before a silent agent is critical.
- `VSA_ALERT_IGNORE_CONTAINERS` — name substrings to skip (e.g. known
  cosmetic-unhealthy `reverse-proxy-nginx`, `*promtail`).
- `VSA_ALERT_PROMETHEUS_URL` — disk-metrics source (default `http://localhost:9090`).
- `VSA_ALERT_DISK_WARN_PERCENT` / `VSA_ALERT_DISK_CRIT_PERCENT` — disk-usage
  thresholds (default 85 / 92).
- `VSA_ALERT_DISK_MOUNTS` — PromQL regex of mountpoints to watch (default
  `/|/var/lib/docker`).

## Rotating the SMTP password

Edit `VSA_ALERT_SMTP_PASSWORD` in `/etc/vsa/alert.env`, then
`vsa alert test`. No restart needed — each `vsa alert` run reads the env fresh.

## Notes / future

- Transport is SMTP-only today. To add Slack/Telegram, extend
  `services/alerting.py:send_email` (the `Problem` list + render functions are
  transport-agnostic).
- The state file makes alerts idempotent per problem-set; if you want a daily
  "all clear" heartbeat, add a second timer running `vsa alert check --force`.
