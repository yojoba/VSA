# ADR-001: Replace Bash Scripts with Python CLI

## Status
Accepted

## Context
The project relied on 12+ Bash scripts in `infra/scripts/` for provisioning, SSL management, auth, and vhost sync. These scripts had:
- ~60% code duplication between `provision_site.sh` and `provision_container.sh`
- Hardcoded paths (`/home/fgrosal/dev/github/VSA`)
- Fragile sed-based placeholder substitution for NGINX configs
- Insecure APR1 (MD5-based) password hashing
- No audit trail of operations
- Silent failures via `|| true` patterns

## Decision
Replace all Bash scripts with a unified Python CLI (`vsa`) using Typer, with:
- **Jinja2 templates** for NGINX vhost generation (safe, composable, testable)
- **bcrypt** for htpasswd generation (replaces APR1/MD5)
- **Pydantic config** with `VSA_ROOT` environment variable (eliminates hardcoded paths)
- **Structured audit logging** to JSONL + SQLite
- **`nginx -t` validation** before any reload

## Consequences
- All infrastructure operations are auditable
- Vhost generation is testable with unit tests (30 tests in Phase 1)
- New team members use a single `vsa` command instead of memorizing script paths
- Bash scripts remain in `infra/scripts/` during a transition period
- One-off scripts moved to `infra/scripts/_deprecated/`
