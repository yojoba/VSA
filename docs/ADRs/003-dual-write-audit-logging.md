# ADR-003: Dual-Write Audit Logging (JSONL + SQLite)

## Status
Accepted

## Context
Zero audit trail existed for provisioning operations. For ISO 27001 compliance and operational visibility, all infrastructure changes need to be logged with actor, action, target, result, and timing.

## Decision
Every CLI operation writes audit events to two destinations:
1. **JSONL file** (`/var/log/vsa/audit.jsonl`) — scraped by Promtail into Loki for Grafana dashboards
2. **SQLite database** (`/var/lib/vsa/audit.db`) — queryable locally, synced to the dashboard API

The audit context manager wraps operations to capture timing and success/failure automatically.

## Consequences
- All operations are traceable to an actor and timestamp
- Loki provides centralized querying across VPS nodes
- SQLite provides local querying without network dependency
- The dashboard API can aggregate audit logs from multiple VPS nodes
- Storage overhead is minimal (~100 bytes per event)
