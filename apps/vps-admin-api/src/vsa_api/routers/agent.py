"""Agent sync endpoints — receive data from remote VPS agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.config import settings
from vsa_api.db.session import get_db
from vsa_api.db.tables import AuditLog, Certificate, ContainerSnapshot, Domain, TrafficStat, VpsNode

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


class DomainSyncPayload(BaseModel):
    vps_id: str
    domains: list[dict[str, Any]]


class TrafficSyncPayload(BaseModel):
    vps_id: str
    stats: list[dict[str, Any]]


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
    """Receive container snapshot from a remote VPS agent (full replacement)."""
    # Delete stale snapshots for this VPS before inserting fresh ones
    await db.execute(
        delete(ContainerSnapshot).where(
            ContainerSnapshot.vps_id == payload.vps_id
        )
    )

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
    """Receive certificate status from a remote VPS agent (full reconciliation).

    Upserts certs present in the payload and removes stale entries for this VPS.
    """
    synced_domains: set[str] = set()
    count = 0
    for cert_data in payload.certs:
        domain = cert_data.get("domain", "")
        if not domain:
            continue

        synced_domains.add(domain)

        result = await db.execute(
            select(Certificate).where(Certificate.domain == domain)
        )
        cert = result.scalar_one_or_none()

        expiry_raw = cert_data.get("expiry")
        expiry = None
        if expiry_raw:
            try:
                expiry = datetime.fromisoformat(expiry_raw)
            except (ValueError, TypeError):
                pass

        if cert:
            cert.issuer = cert_data.get("issuer", "Let's Encrypt")
            cert.expiry = expiry
            cert.status = cert_data.get("status", "valid")
        else:
            cert = Certificate(
                domain=domain,
                issuer=cert_data.get("issuer", "Let's Encrypt"),
                expiry=expiry,
                status=cert_data.get("status", "valid"),
            )
            db.add(cert)
        count += 1

    # Remove stale certs: entries for domains no longer reported by this agent
    stale = await db.execute(
        select(Certificate).where(
            Certificate.domain.notin_(synced_domains) if synced_domains else True,
        )
    )
    for orphan in stale.scalars().all():
        await db.delete(orphan)

    await db.commit()
    return {"synced": count}


@router.post("/agent/domains-sync")
async def agent_domains_sync(
    payload: DomainSyncPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive domain list from a remote VPS agent (full reconciliation).

    Upserts domains present in the payload and removes any domains for this
    VPS that are no longer reported (i.e. their vhost was deleted).
    """
    synced_domains: set[str] = set()
    count = 0
    for d in payload.domains:
        domain_name = d.get("domain", "")
        if not domain_name:
            continue

        synced_domains.add(domain_name)

        result = await db.execute(
            select(Domain).where(Domain.domain == domain_name)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.container = d.get("container", existing.container)
            existing.port = d.get("port", existing.port)
            existing.vps_id = payload.vps_id
            existing.status = "active"
        else:
            existing = Domain(
                domain=domain_name,
                container=d.get("container", ""),
                port=d.get("port", 3000),
                vps_id=payload.vps_id,
                status="active",
            )
            db.add(existing)
        count += 1

    # Remove stale domains: entries for this VPS that are no longer in vhost files
    stale = await db.execute(
        select(Domain).where(
            Domain.vps_id == payload.vps_id,
            Domain.domain.notin_(synced_domains) if synced_domains else True,
        )
    )
    for orphan in stale.scalars().all():
        await db.delete(orphan)

    await db.commit()
    return {"synced": count}


@router.get("/agent/vps")
async def list_vps_nodes(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """List all registered VPS nodes (token-authenticated for CLI use)."""
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


@router.delete("/agent/vps/{vps_id}")
async def remove_vps(
    vps_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Remove a VPS node and all its associated data (domains, certs, snapshots)."""
    # Delete associated data
    await db.execute(delete(Domain).where(Domain.vps_id == vps_id))
    await db.execute(
        delete(ContainerSnapshot).where(ContainerSnapshot.vps_id == vps_id)
    )
    await db.execute(delete(TrafficStat).where(TrafficStat.vps_id == vps_id))

    # Delete the node itself
    result = await db.execute(
        delete(VpsNode).where(VpsNode.vps_id == vps_id)
    )
    await db.commit()

    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail=f"VPS '{vps_id}' not found")

    return {"status": "ok", "vps_id": vps_id}


@router.post("/agent/traffic-sync")
async def agent_traffic_sync(
    payload: TrafficSyncPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive aggregated traffic stats from a remote VPS agent."""
    count = 0
    for stat in payload.stats:
        period_start = stat.get("period_start", "")
        period_end = stat.get("period_end", "")
        try:
            ps = datetime.fromisoformat(period_start) if period_start else datetime.now(timezone.utc)
            pe = datetime.fromisoformat(period_end) if period_end else datetime.now(timezone.utc)
        except (ValueError, TypeError):
            ps = datetime.now(timezone.utc)
            pe = datetime.now(timezone.utc)

        entry = TrafficStat(
            domain=stat.get("domain", ""),
            vps_id=payload.vps_id,
            period_start=ps,
            period_end=pe,
            requests=stat.get("requests", 0),
            status_2xx=stat.get("status_2xx", 0),
            status_3xx=stat.get("status_3xx", 0),
            status_4xx=stat.get("status_4xx", 0),
            status_5xx=stat.get("status_5xx", 0),
            bytes_sent=stat.get("bytes_sent", 0),
            avg_request_time_ms=stat.get("avg_request_time_ms", 0),
        )
        db.add(entry)
        count += 1

    await db.commit()
    return {"synced": count}
