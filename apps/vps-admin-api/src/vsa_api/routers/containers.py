"""Container endpoints — backed by agent snapshots in the hub DB.

Each remote VPS runs a ``vsa agent`` systemd timer that POSTs the local
``docker ps -a`` snapshot to ``/agent/containers-sync`` every 30s. This
router reads from that ``container_snapshots`` table so the dashboard
shows containers across the whole fleet, not just the host the API
itself happens to run on.

Freshness is bounded by the agent tick (~30s).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import ContainerSnapshot

router = APIRouter(tags=["containers"])


def _serialize(c: ContainerSnapshot) -> dict[str, object]:
    return {
        "vps_id": c.vps_id,
        "name": c.container_name,
        "image": c.image,
        "status": c.status,
        "ports": c.ports,
        "compose_project": c.compose_project,
        "snapshot_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/containers")
async def list_containers(db: AsyncSession = Depends(get_db)):
    """List every known container across the VPS fleet, ordered by VPS then name."""
    result = await db.execute(
        select(ContainerSnapshot).order_by(
            ContainerSnapshot.vps_id, ContainerSnapshot.container_name
        )
    )
    return [_serialize(c) for c in result.scalars().all()]


@router.get("/containers/{name}")
async def get_container(
    name: str,
    db: AsyncSession = Depends(get_db),
    vps_id: str | None = Query(
        None,
        description=(
            "Disambiguate when the same container name exists on multiple VPS. "
            "If omitted, the first match is returned."
        ),
    ),
):
    """Get the latest snapshot for a specific container.

    Container names are typically unique within a VPS but not necessarily
    across the fleet, so callers SHOULD pass ``vps_id`` when available.
    """
    stmt = select(ContainerSnapshot).where(ContainerSnapshot.container_name == name)
    if vps_id:
        stmt = stmt.where(ContainerSnapshot.vps_id == vps_id)
    stmt = stmt.order_by(ContainerSnapshot.created_at.desc())

    result = await db.execute(stmt)
    snapshot = result.scalars().first()
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Container '{name}' not found")
    return _serialize(snapshot)
