# VSA Dashboard API

FastAPI backend for the centralized VSA management dashboard. Provides live container queries, domain/certificate registry, paginated audit logs, and multi-VPS agent sync endpoints.

## Stack

- **FastAPI** + **Uvicorn** — async HTTP server
- **SQLAlchemy 2** (async) + **asyncpg** — PostgreSQL ORM
- **Alembic** — database migrations
- **Docker SDK** — live container queries via Docker socket

## API Endpoints

```
GET  /api/health              # Health check
GET  /api/containers          # Live container list (Docker socket)
GET  /api/containers/{name}   # Container details
GET  /api/domains             # Domain registry
GET  /api/certs               # Certificate status
GET  /api/certs/expiring      # Certs expiring within N days
GET  /api/audit-logs          # Paginated, filterable audit trail
GET  /api/audit-logs/export   # CSV/JSON export (ISO compliance)
GET  /api/stacks              # Compose stack status
GET  /api/vps                 # Multi-VPS node list
POST /api/agent/heartbeat     # Agent registration/heartbeat
POST /api/agent/audit-sync    # Batch audit event sync
POST /api/agent/containers-sync
POST /api/agent/certs-sync
```

## Database Tables

- `vps_nodes` — VPS registry (id, hostname, IP, status, last_seen)
- `domains` — domain registry (domain, vps_id, container, port, status)
- `certificates` — cert status (domain, issuer, expiry, status)
- `audit_logs` — full audit trail (timestamp, actor, action, target, result)
- `container_snapshots` — periodic container state snapshots

## Development

```bash
uv sync
uv run vsa-api          # Start dev server on :8000
```

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `VSA_DATABASE_URL` | `postgresql+asyncpg://vsa:vsa@localhost:5432/vsa` | Database connection |
| `VSA_DOCKER_SOCKET` | `unix:///var/run/docker.sock` | Docker socket path |
| `VSA_CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins |
| `VSA_API_TOKEN` | (empty) | Pre-shared token for agent auth |

## Deployment

Deployed as part of the dashboard stack at `stacks/dashboard/`. See `stacks/dashboard/README.md`.
