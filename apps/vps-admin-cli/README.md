# vsa — VPS Admin CLI

Unified CLI replacing all bash scripts for managing multi-tenant hosting infrastructure.

## Install

```bash
cd apps/vps-admin-cli
uv tool install .
```

Or for development:

```bash
uv sync
uv run vsa --help
```

## Commands

```bash
# Site provisioning
vsa site provision --domain example.com --container web-1 --port 3000
vsa site provision --domain example.com --port 3000 --detect --external-port 3005
vsa site unprovision --domain example.com
vsa site list

# SSL certificates
vsa cert issue --domain example.com
vsa cert renew
vsa cert status
vsa cert install-cron

# HTTP Basic Auth (bcrypt)
vsa auth add --domain example.com --user admin
vsa auth remove --domain example.com
vsa auth list

# NGINX vhost management
vsa vhost sync
vsa vhost list
vsa vhost show example.com

# Docker Compose stacks
vsa stack new my-stack
vsa stack up my-stack
vsa stack down my-stack
vsa stack logs my-stack
vsa stack ps my-stack

# VPS bootstrap
vsa bootstrap

# Multi-VPS agent
vsa agent register --hub-url https://dashboard.flowbiz.ai/api --token XXX
vsa agent start
vsa agent status
```

## Architecture

```
src/vsa/
  cli.py              # Root Typer app
  config.py           # VsaConfig singleton (VSA_ROOT env var)
  audit.py            # Dual-write JSONL + SQLite audit logger
  errors.py           # Custom exceptions
  commands/           # site, cert, auth, stack, vhost, bootstrap, agent
  services/           # docker, nginx, certbot, htpasswd, vhost_renderer, network
  templates/          # Jinja2 NGINX vhost templates
  models/             # Re-exported Pydantic models
```

## Key Design Decisions

- **Jinja2 templates** for vhost generation (replaces sed placeholder substitution)
- **bcrypt** for htpasswd entries (replaces APR1/MD5)
- **`nginx -t` before reload** — validates config before applying
- **Structured audit logging** to `/var/log/vsa/audit.jsonl` + `/var/lib/vsa/audit.db`
- **Pydantic config** with `VSA_ROOT` env var — no hardcoded paths

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `VSA_ROOT` | auto-detected | Repository root path |
| `VSA_VPS_ID` | `vps-01` | VPS identifier for audit logs |
| `VSA_CERTBOT_EMAIL` | `ops@flowbiz.ai` | Email for Let's Encrypt |
| `VSA_ACTOR` | current OS user | Actor name in audit events |

## Tests

```bash
uv run pytest -q        # 30 tests
uv run pytest -v        # verbose
uv run ruff check .     # lint
```
