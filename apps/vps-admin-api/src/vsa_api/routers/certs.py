"""Certificate status endpoints — backed by the agent-synced ``certificates`` table.

Each VPS agent scans its local Let's Encrypt store and POSTs the result
to ``/agent/certs-sync`` (~30s tick). This router reads from the resulting
table so the dashboard reflects every cert across the fleet.

A given domain may legitimately have a cert on multiple VPS (warm
standby pattern), so each row is identified by ``(vps_id, domain)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import Certificate

router = APIRouter(tags=["certificates"])


def _days_remaining(expiry: datetime | None) -> int | None:
    if expiry is None:
        return None
    return (expiry - datetime.now(timezone.utc)).days


def _serialize(c: Certificate) -> dict[str, object]:
    return {
        "vps_id": c.vps_id,
        "domain": c.domain,
        "issuer": c.issuer,
        "expiry": c.expiry.isoformat() if c.expiry else None,
        "days_remaining": _days_remaining(c.expiry),
        "status": c.status,
    }


@router.get("/certs")
async def list_certs(db: AsyncSession = Depends(get_db)):
    """List every certificate across the fleet, ordered by VPS then domain."""
    result = await db.execute(
        select(Certificate).order_by(Certificate.vps_id, Certificate.domain)
    )
    return [_serialize(c) for c in result.scalars().all()]


@router.get("/certs/expiring")
async def expiring_certs(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """List certificates expiring within N days, across the whole fleet."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=days)
    result = await db.execute(
        select(Certificate)
        .where(Certificate.expiry.is_not(None), Certificate.expiry <= cutoff)
        .order_by(Certificate.expiry)
    )
    return [_serialize(c) for c in result.scalars().all()]
