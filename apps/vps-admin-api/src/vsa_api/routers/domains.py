"""Domain registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import Domain

router = APIRouter(tags=["domains"])


@router.get("/domains")
async def list_domains(db: AsyncSession = Depends(get_db)):
    """List all registered domains."""
    result = await db.execute(select(Domain).order_by(Domain.domain))
    domains = result.scalars().all()
    return [
        {
            "id": d.id,
            "domain": d.domain,
            "vps_id": d.vps_id,
            "container": d.container,
            "port": d.port,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in domains
    ]
