"""Certificate status endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import Certificate

router = APIRouter(tags=["certificates"])


@router.get("/certs")
async def list_certs(db: AsyncSession = Depends(get_db)):
    """List all certificates and their expiry status."""
    result = await db.execute(select(Certificate).order_by(Certificate.domain))
    certs = result.scalars().all()
    return [
        {
            "id": c.id,
            "domain": c.domain,
            "issuer": c.issuer,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "status": c.status,
        }
        for c in certs
    ]


@router.get("/certs/expiring")
async def expiring_certs(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """List certificates expiring within N days."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=days)
    result = await db.execute(
        select(Certificate)
        .where(Certificate.expiry <= cutoff)
        .order_by(Certificate.expiry)
    )
    certs = result.scalars().all()
    return [
        {
            "domain": c.domain,
            "expiry": c.expiry.isoformat() if c.expiry else None,
            "days_remaining": (c.expiry - datetime.now(timezone.utc)).days if c.expiry else None,
        }
        for c in certs
    ]
