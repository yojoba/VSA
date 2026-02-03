# ADR-004: Hub-and-Agent Model for Multi-VPS Management

## Status
Accepted

## Context
The architecture needs to scale from a single VPS (Infomaniak) to multiple nodes (including legacy Kamatera). A centralized dashboard needs visibility into all nodes.

## Decision
Adopt a hub-and-agent model:
- **Hub**: Dashboard stack on VPS-01, running FastAPI + PostgreSQL + Next.js
- **Agents**: The `vsa` CLI running on each VPS with `vsa agent start` via systemd timer

Agents send periodic heartbeats, container snapshots, certificate status, and audit events to the hub via pre-shared API token authentication.

## Consequences
- Each VPS operates independently (CLI works without the hub)
- The dashboard aggregates data from all nodes
- Adding a new VPS requires only: install CLI, run `vsa agent register`, enable systemd timer
- Authentication is simple (pre-shared tokens), upgradeable to mTLS later
- Network partitions don't affect local operations, only dashboard visibility
