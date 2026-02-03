"""Shared constants for the VSA ecosystem."""

from pathlib import Path

# Docker networking
DOCKER_NETWORK = "flowbiz_ext"

# Default paths (overridable via VsaConfig / env vars)
SRV_BASE = Path("/srv/flowbiz")

# Reverse proxy paths (relative to SRV_BASE)
NGINX_CONF_DIR = "reverse-proxy/nginx/conf.d"
NGINX_SNIPPETS_DIR = "reverse-proxy/nginx/snippets"
NGINX_AUTH_DIR = "reverse-proxy/nginx/auth"

# Audit / logging
LOG_DIR = Path("/var/log/vsa")
AUDIT_JSONL_PATH = LOG_DIR / "audit.jsonl"
AUDIT_DB_PATH = Path("/var/lib/vsa/audit.db")

# Certbot
CERTBOT_EMAIL = "ops@flowbiz.ai"

# NGINX defaults
DEFAULT_CLIENT_MAX_BODY_SIZE = "20m"
DEFAULT_PROXY_READ_TIMEOUT = "120s"
DEFAULT_PROXY_SEND_TIMEOUT = "120s"
