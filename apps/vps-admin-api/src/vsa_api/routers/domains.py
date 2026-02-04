"""Domain registry endpoints — reads live from vhost files on disk."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["domains"])

_VHOST_DIR = Path("/etc/nginx/conf.d")
_UPSTREAM_RE = re.compile(r"set\s+\$\w*upstream\s+(.+?):(\d+)\s*;")


@router.get("/domains")
async def list_domains():
    """List all active domains by scanning NGINX vhost config files on disk.

    This is the live source of truth — only domains with a vhost config are active.
    """
    domains: list[dict] = []

    if not _VHOST_DIR.is_dir():
        return domains

    for conf in sorted(_VHOST_DIR.glob("*.conf")):
        domain = conf.stem
        # Skip NGINX internal configs (no dots = not a domain)
        if "." not in domain:
            continue

        content = conf.read_text()
        match = _UPSTREAM_RE.search(content)
        container = match.group(1) if match else ""
        port = int(match.group(2)) if match else 0

        domains.append({
            "domain": domain,
            "container": container,
            "port": port,
            "status": "active",
        })

    return domains
