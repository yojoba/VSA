"""Domain registry endpoints — backed by the agent-synced ``domains`` table.

Each VPS agent scans its local NGINX vhost dir and POSTs the result to
``/agent/domains-sync`` (~30s tick). This router reads from the resulting
table so the dashboard reflects the whole fleet, not just the host the
API runs on.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import Domain

router = APIRouter(tags=["domains"])


@router.get("/domains")
async def list_domains(db: AsyncSession = Depends(get_db)):
    """List every domain known across the VPS fleet, ordered by VPS then domain."""
    result = await db.execute(
        select(Domain).order_by(Domain.vps_id, Domain.domain)
    )
    return [
        {
            "id": d.id,
            "vps_id": d.vps_id,
            "domain": d.domain,
            "container": d.container,
            "port": d.port,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in result.scalars().all()
    ]
