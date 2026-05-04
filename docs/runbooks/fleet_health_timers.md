# Fleet Health Timers Runbook

Two systemd timers that drive the fleet-wide checks built in Phase A→E:

- **`vsa-drift.timer`** — daily 08:00 local, runs `vsa fleet drift`.
  Cross-checks the user's intent (`domain_assignments` registry) against
  agent-observed state (`domains` + `certificates` tables) and flags
  drift (rogue hosts, missing certs/vhosts, expiring certs, etc.).

- **`vsa-fleet-cert-health.timer`** — weekly Monday 09:00, runs
  `vsa fleet cert-health --all`. Iterates every registered VPS, runs
  `vsa cert health` remotely via the hub→agent execution channel, and
  reports any broken-symlink / no-LE-account / expiring-cert finding.

Both run on the **hub** only — they invoke `vsa fleet …` which goes
through the hub API.

## When to use

- Default for any VSA-managed fleet. Without these, problems like the
  `lokalflash.ch` rogue-host or the vps-02 broken symlinks (both hit
  during the 2026-05-04 session) only surface when someone manually
  runs `vsa fleet drift`.

## Prerequisites

- The hub already runs `vsa-agent.timer` (i.e. `vsa-cli` is installed
  + `/etc/vsa/agent.env` is populated).
- `/etc/vsa/agent.env` includes the hub-side vars:
  `VSA_HUB_URL=https://dashboard.flowbiz.ai/api` and
  `VSA_HUB_AUTH=admin:<basic-auth-pass>`. The systemd services load
  this file via `EnvironmentFile=`.

## Install (on the hub)

```bash
cd ~/dev/github/VSA
sudo cp infra/systemd/vsa-drift.{service,timer} /etc/systemd/system/
sudo cp infra/systemd/vsa-fleet-cert-health.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vsa-drift.timer vsa-fleet-cert-health.timer
```

Verify the next run is scheduled:

```bash
systemctl list-timers vsa-drift.timer vsa-fleet-cert-health.timer
```

## Trigger ad-hoc

```bash
sudo systemctl start vsa-drift.service
sudo systemctl start vsa-fleet-cert-health.service
```

Read the result via journalctl:

```bash
sudo journalctl -u vsa-drift.service -n 50 --no-pager
sudo journalctl -u vsa-fleet-cert-health.service -n 50 --no-pager
```

Both services exit non-zero on any critical finding, so the unit's
state in `systemctl status` reflects fleet health.

## Alerting

Today the systemd unit's failure state is the alert signal — `systemctl
status` shows `failed` and journalctl carries the full output. Two
upgrade paths when you're ready for active alerts:

1. **`OnFailure=` chain.** Drop a `vsa-drift-alert@.service` unit that
   POSTs to a Slack/webhook URL, then add `OnFailure=vsa-drift-alert@%n.service`
   to `vsa-drift.service`. systemd handles the dispatch.

2. **Loki alerting rule.** Output already lands in journalctl which the
   hub's Promtail scrapes (`job=systemd`) and ships to the central
   Loki at `loki.flowbiz.ai`. Add a Loki alerting rule
   matching `level="critical"` lines from `unit="vsa-drift.service"` and
   route to email / Slack / Grafana OnCall.

The Loki path is more flexible (rate limiting, per-finding severity,
correlation with other logs) and reuses what's already deployed.

## Tuning the schedule

Edit the `OnCalendar=` line in the `.timer` file, then
`sudo systemctl daemon-reload && sudo systemctl restart <name>.timer`.

- Drift every 6h instead of daily: `OnCalendar=*-*-* 00,06,12,18:00:00`
- Cert-health twice a week: `OnCalendar=Mon,Thu *-*-* 09:00:00`

`RandomizedDelaySec=` smears multiple hub timers so they don't all hit
the API in the same second. Bump it on a heavily loaded hub.
