# ADR-002: Jinja2 Templates for NGINX Vhost Generation

## Status
Accepted

## Context
The original scripts used heredocs with sed placeholder replacement (`DOMAIN_PLACEHOLDER`, `CONTAINER_PLACEHOLDER`) to generate NGINX configs. This approach:
- Broke on domain names containing regex-special characters
- Required careful ordering of replacements (WWW_DOMAIN before DOMAIN)
- Was untestable without running actual provisioning

## Decision
Use Jinja2 templates (`vhost_https.conf.j2`, `vhost_http.conf.j2`) with a `SiteConfig` Pydantic model. The renderer receives a typed config object and produces valid NGINX config.

## Consequences
- Template rendering is unit-testable (no Docker or NGINX required)
- Special characters in domains/containers are handled safely
- Templates are readable and match the final output format
- Adding new features (WebSocket support, custom headers) is a template change, not a sed expression
