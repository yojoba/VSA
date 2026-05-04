"""Docker Compose stack overview — reconstructed from agent snapshots.

A "stack" is the set of containers that share a ``com.docker.compose.project``
label. Since the same stack name (e.g. ``reverse-proxy``) can run
independently on multiple VPS, we group by ``(vps_id, compose_project)``
so each instance appears as a distinct entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import ContainerSnapshot

router = APIRouter(tags=["stacks"])


@router.get("/stacks")
async def list_stacks(db: AsyncSession = Depends(get_db)):
    """List compose stacks per VPS, grouped from container snapshots."""
    result = await db.execute(
        select(ContainerSnapshot)
        .where(ContainerSnapshot.compose_project != "")
        .order_by(
            ContainerSnapshot.vps_id,
            ContainerSnapshot.compose_project,
            ContainerSnapshot.container_name,
        )
    )

    stacks: dict[tuple[str, str], list[dict]] = {}
    for c in result.scalars().all():
        key = (c.vps_id, c.compose_project)
        stacks.setdefault(key, []).append(
            {
                "name": c.container_name,
                "service": c.compose_service,
                "status": c.status,
                "image": c.image,
            }
        )

    return [
        {"vps_id": vps_id, "name": project, "containers": containers}
        for (vps_id, project), containers in stacks.items()
    ]
