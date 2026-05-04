"""Domain assignment registry endpoints — primary + standby VPS per domain.

This is the user-edited intent table. Distinct from the agent-synced
``domains`` and ``certificates`` tables, which observe what each VPS has on
disk. ``domain_assignments`` records what the user *wants*: which VPS owns
the domain (primary) and which are warm standbys.

Failover, drift detection, and fleet-aware CLI commands all consult this
table.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import DomainAssignment

router = APIRouter(tags=["assignments"])


class AssignmentPayload(BaseModel):
    """Payload for creating or updating a domain assignment."""

    primary_vps_id: str = Field(..., min_length=1, max_length=64)
    standby_vps_ids: list[str] = Field(default_factory=list)
    notes: str = ""


def _serialize(a: DomainAssignment) -> dict[str, Any]:
    return {
        "id": a.id,
        "domain": a.domain,
        "primary_vps_id": a.primary_vps_id,
        "standby_vps_ids": a.standby_vps_ids,
        "notes": a.notes,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.get("/assignments")
async def list_assignments(db: AsyncSession = Depends(get_db)):
    """List all domain assignments, ordered by domain."""
    result = await db.execute(
        select(DomainAssignment).order_by(DomainAssignment.domain)
    )
    return [_serialize(a) for a in result.scalars().all()]


@router.get("/assignments/{domain}")
async def get_assignment(domain: str, db: AsyncSession = Depends(get_db)):
    """Fetch the assignment for a single domain."""
    result = await db.execute(
        select(DomainAssignment).where(DomainAssignment.domain == domain)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail=f"No assignment for {domain}")
    return _serialize(a)


@router.put("/assignments/{domain}")
async def upsert_assignment(
    domain: str,
    payload: AssignmentPayload,
    db: AsyncSession = Depends(get_db),
):
    """Create or update the assignment for ``domain``.

    Validation:
    - ``primary_vps_id`` may not also appear in ``standby_vps_ids``.
    - ``standby_vps_ids`` is deduplicated.
    """
    standbys = sorted({s for s in payload.standby_vps_ids if s})
    if payload.primary_vps_id in standbys:
        raise HTTPException(
            status_code=400,
            detail=(
                f"primary_vps_id {payload.primary_vps_id!r} cannot also appear "
                f"in standby_vps_ids"
            ),
        )

    result = await db.execute(
        select(DomainAssignment).where(DomainAssignment.domain == domain)
    )
    a = result.scalar_one_or_none()
    if a is None:
        a = DomainAssignment(
            domain=domain,
            primary_vps_id=payload.primary_vps_id,
            standby_vps_ids=standbys,
            notes=payload.notes,
        )
        db.add(a)
    else:
        a.primary_vps_id = payload.primary_vps_id
        a.standby_vps_ids = standbys
        a.notes = payload.notes

    await db.commit()
    await db.refresh(a)
    return _serialize(a)


@router.delete("/assignments/{domain}")
async def delete_assignment(domain: str, db: AsyncSession = Depends(get_db)):
    """Remove the assignment for a domain. Doesn't touch agent-synced data."""
    result = await db.execute(
        select(DomainAssignment).where(DomainAssignment.domain == domain)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail=f"No assignment for {domain}")
    await db.delete(a)
    await db.commit()
    return {"deleted": domain}
