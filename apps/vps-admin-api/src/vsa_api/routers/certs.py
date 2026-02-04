"""Certificate status endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from fastapi import APIRouter, Query

router = APIRouter(tags=["certificates"])

log = logging.getLogger(__name__)

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def _read_cert_expiry(domain: str) -> datetime | None:
    """Read the expiry date from a Let's Encrypt certificate on disk."""
    cert_path = LETSENCRYPT_LIVE / domain / "fullchain.pem"
    if not cert_path.exists():
        return None
    try:
        pem_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(pem_data)
        return cert.not_valid_after_utc
    except Exception as exc:
        log.warning("Failed to read cert for %s: %s", domain, exc)
    return None


def _cert_status(expiry: datetime | None) -> str:
    if expiry is None:
        return "unknown"
    now = datetime.now(timezone.utc)
    days = (expiry - now).days
    if days < 0:
        return "expired"
    if days <= 14:
        return "critical"
    if days <= 30:
        return "warning"
    return "valid"


def _scan_certs() -> list[dict]:
    """Scan all Let's Encrypt certificates on disk."""
    certs = []
    if not LETSENCRYPT_LIVE.is_dir():
        log.warning("Let's Encrypt directory not found: %s", LETSENCRYPT_LIVE)
        return certs

    for entry in sorted(LETSENCRYPT_LIVE.iterdir()):
        if not entry.is_dir() or entry.name == "README":
            continue
        domain = entry.name
        expiry = _read_cert_expiry(domain)
        status = _cert_status(expiry)
        days_remaining = (expiry - datetime.now(timezone.utc)).days if expiry else None
        certs.append(
            {
                "domain": domain,
                "issuer": "Let's Encrypt",
                "expiry": expiry.isoformat() if expiry else None,
                "days_remaining": days_remaining,
                "status": status,
            }
        )
    return certs


@router.get("/certs")
async def list_certs():
    """List all certificates and their expiry status from disk."""
    return _scan_certs()


@router.get("/certs/expiring")
async def expiring_certs(
    days: int = Query(default=30, ge=1, le=365),
):
    """List certificates expiring within N days."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=days)
    return [
        c
        for c in _scan_certs()
        if c["expiry"] and datetime.fromisoformat(c["expiry"]) <= cutoff
    ]
