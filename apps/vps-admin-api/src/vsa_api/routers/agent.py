"""Agent sync endpoints — receive data from remote VPS agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.config import settings
from vsa_api.db.session import get_db
from vsa_api.db.tables import AuditLog, ContainerSnapshot, VpsNode

router = APIRouter(tags=["agent"])


def _verify_token(authorization: str = Header("")):
    if not settings.api_token:
        return  # No token configured = open (dev mode)
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=401, detail="Invalid agent token")


class HeartbeatPayload(BaseModel):
    vps_id: str
    hostname: str = ""
    ip_address: str = ""


class AuditSyncPayload(BaseModel):
    events: list[dict[str, Any]]


class ContainerSyncPayload(BaseModel):
    vps_id: str
    containers: list[dict[str, Any]]


class CertSyncPayload(BaseModel):
    vps_id: str
    certs: list[dict[str, Any]]


@router.post("/agent/heartbeat")
async def agent_heartbeat(
    payload: HeartbeatPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Register or update a VPS agent heartbeat."""
    result = await db.execute(
        select(VpsNode).where(VpsNode.vps_id == payload.vps_id)
    )
    node = result.scalar_one_or_none()

    if node:
        node.hostname = payload.hostname or node.hostname
        node.ip_address = payload.ip_address or node.ip_address
        node.status = "active"
        node.last_seen = datetime.now(timezone.utc)
    else:
        node = VpsNode(
            vps_id=payload.vps_id,
            hostname=payload.hostname,
            ip_address=payload.ip_address,
            status="active",
        )
        db.add(node)

    await db.commit()
    return {"status": "ok"}


@router.post("/agent/audit-sync")
async def agent_audit_sync(
    payload: AuditSyncPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive batch audit events from a remote VPS agent."""
    count = 0
    for event_data in payload.events:
        log = AuditLog(
            timestamp=event_data.get("timestamp", datetime.now(timezone.utc)),
            vps_id=event_data.get("vps_id", ""),
            actor=event_data.get("actor", ""),
            action=event_data.get("action", ""),
            target=event_data.get("target", ""),
            params=str(event_data.get("params", "{}")),
            result=event_data.get("result", "success"),
            error=event_data.get("error"),
            duration_ms=event_data.get("duration_ms"),
        )
        db.add(log)
        count += 1

    await db.commit()
    return {"synced": count}


@router.post("/agent/containers-sync")
async def agent_containers_sync(
    payload: ContainerSyncPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive container snapshot from a remote VPS agent."""
    for c in payload.containers:
        snapshot = ContainerSnapshot(
            vps_id=payload.vps_id,
            container_name=c.get("name", ""),
            image=c.get("image", ""),
            status=c.get("status", ""),
            ports=str(c.get("ports", "")),
        )
        db.add(snapshot)

    await db.commit()
    return {"synced": len(payload.containers)}


@router.post("/agent/certs-sync")
async def agent_certs_sync(
    payload: CertSyncPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive certificate status from a remote VPS agent."""
    return {"synced": len(payload.certs), "status": "accepted"}
