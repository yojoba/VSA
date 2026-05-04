# ADR-006: Hub→Agent Execution Channel

**Status:** Accepted
**Date:** 2026-05-04

## Context

After ADR-004 introduced the hub-and-agent model, agents pushed state to the
hub (containers, certs, domains, traffic, audit) but had no way to receive
instructions back. Every multi-VPS write operation required SSH'ing into each
VPS and running `vsa …` by hand.

The 2026-05-04 session needed to provision warm-standby certs across multiple
VPS, sync configs to standbys, and trigger fleet-wide health checks. Doing
this via SSH was workable but:

- The hub already has a queryable inventory of VPS — it should orchestrate them.
- SSH-based fan-out doesn't audit-log on the hub, only on the receiving VPS.
- Multi-step orchestrators (e.g. "provision on primary, then provision
  --standby on each standby") are awkward to express in shell.

## Decision

Add a hub-stored command queue that agents poll on each sync tick (~30s).

- New table `agent_commands(id, vps_id, argv JSON, status, timeout_seconds,
  requested_by, created_at, taken_at, completed_at, exit_code, stdout, stderr)`.
- New endpoints under `/api/agent/`:
  - `POST /agent/exec` — caller-side enqueue (basic-auth)
  - `GET /agent/commands?vps_id=X&status=pending` — agent poll (token-auth)
  - `POST /agent/commands/{id}/take` — agent atomic claim (409 if taken)
  - `POST /agent/commands/{id}/result` — agent reports back (token-auth)
  - `GET /agent/commands/{id}` — caller polls completion (basic-auth)
- New CLI command `vsa fleet exec --vps X -- <argv>` runs on the hub,
  enqueues a command, polls the result, streams stdout/stderr back, exits
  with the remote exit code.
- New agent step `sync_pending_commands` joins the existing sync loop.
  It calls `subprocess.run(["vsa", *argv], …)` with the command-specified
  timeout, captures stdout/stderr (64 KB cap), reports back.

The agent always prepends `vsa` — it does **not** execute arbitrary shell.
That bounds the attack surface to whatever `vsa` itself can do, which the
operator already controls (the agent runs as root + has the docker socket
anyway).

## Consequences

**Pros:**

- Fleet orchestrators (`vsa fleet site-provision`, `vhost-sync`,
  `cert-renew`, `cert-health --all`) become single CLI invocations on the
  hub, with central audit logging.
- Pull-based: no inbound network access from the hub to the VPS — the
  hub never opens a connection to a VPS, agents always initiate.
- Reuses the existing agent sync tick, so no new daemon or systemd unit.

**Cons:**

- Up to ~30s of latency before a command is picked up (one agent tick).
  Acceptable for fleet ops; not for interactive use cases.
- Output is captured-then-replayed, not streamed live. Long-running
  commands appear silent until they complete.
- A crashed agent leaves commands in `running` state forever. A reaper
  (cron job marking long-`running` commands as `timeout`) is left for
  v2.
- The `take` endpoint is racy on its own — two agents would both succeed
  if they happen to poll at the exact same moment with no advisory lock.
  In practice each command targets a single `vps_id` and only that VPS's
  agent polls it, so this hasn't been an issue. A `SELECT FOR UPDATE
  SKIP LOCKED` upgrade is a future v2 if we ever want command-pool
  workers.

## Alternatives considered

- **SSH from hub to agent.** Simpler to implement but the hub then needs
  outbound SSH credentials per VPS, and audit logging splits across two
  hosts. Pull-based queue keeps everything in one DB.
- **Server-Sent Events / WebSocket from hub to agent.** True streaming
  but adds a long-lived connection, complicates the API container's
  process model, and the agent already has a 30s heartbeat — close
  enough to "real-time" for the use cases.
- **Run commands directly from a hub-side service (no agent involvement).**
  Doesn't work — many `vsa` commands inspect the local docker socket,
  filesystem, or systemd. They have to run *on* the target host.

## Future work

- Failover (`vsa fleet site failover --domain X --to Z`): orchestrates
  promotion of a standby to primary. Will use the same exec channel
  plus DNS updates via the Cloudflare API.
- Reaper: cron job that marks `running` commands older than `timeout
  seconds + 60s grace` as `timeout`.
- Streaming output: WebSocket upgrade once a single command exceeds the
  current 64 KB cap regularly.
- Whitelist of allowed argv prefixes (security hardening) once we have
  multiple operators.
