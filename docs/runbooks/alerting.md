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

Only problems at or above `VSA_ALERT_MIN_LEVEL` (default `warning`) are kept.

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

## Configuration reference

All `VSA_ALERT_*` env vars live in `/etc/vsa/alert.env`. See
`infra/systemd/alert.env.example` for the full annotated list. The most useful:

- `VSA_ALERT_TO` — comma-separated recipients.
- `VSA_ALERT_MIN_LEVEL` — `info` | `warning` | `critical`.
- `VSA_ALERT_AGENT_STALE_MINUTES` — how long before a silent agent is critical.
- `VSA_ALERT_IGNORE_CONTAINERS` — name substrings to skip (e.g. known
  cosmetic-unhealthy `reverse-proxy-nginx`, `*promtail`).

## Rotating the SMTP password

Edit `VSA_ALERT_SMTP_PASSWORD` in `/etc/vsa/alert.env`, then
`vsa alert test`. No restart needed — each `vsa alert` run reads the env fresh.

## Notes / future

- Transport is SMTP-only today. To add Slack/Telegram, extend
  `services/alerting.py:send_email` (the `Problem` list + render functions are
  transport-agnostic).
- The state file makes alerts idempotent per problem-set; if you want a daily
  "all clear" heartbeat, add a second timer running `vsa alert check --force`.
