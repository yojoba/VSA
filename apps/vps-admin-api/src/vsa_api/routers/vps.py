"""VPS node registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import VpsNode

router = APIRouter(tags=["vps"])


@router.get("/vps")
async def list_vps_nodes(db: AsyncSession = Depends(get_db)):
    """List all registered VPS nodes."""
    result = await db.execute(select(VpsNode).order_by(VpsNode.vps_id))
    nodes = result.scalars().all()
    return [
        {
            "id": n.id,
            "vps_id": n.vps_id,
            "hostname": n.hostname,
            "ip_address": n.ip_address,
            "status": n.status,
            "last_seen": n.last_seen.isoformat() if n.last_seen else None,
        }
        for n in nodes
    ]
