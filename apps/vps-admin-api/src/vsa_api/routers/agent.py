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
from vsa_api.db.tables import (
    AgentCommand,
    AuditLog,
    Certificate,
    ContainerSnapshot,
    Domain,
    TrafficStat,
    VpsNode,
)

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
            compose_project=c.get("compose_project", ""),
            compose_service=c.get("compose_service", ""),
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

    Upserts certs present in the payload for ``payload.vps_id`` and removes
    stale entries for THAT VPS only — never touching certs owned by other
    VPS (the same domain can be pre-deployed on a warm-standby host).
    """
    synced_domains: set[str] = set()
    count = 0
    for cert_data in payload.certs:
        domain = cert_data.get("domain", "")
        if not domain:
            continue

        synced_domains.add(domain)

        result = await db.execute(
            select(Certificate).where(
                Certificate.domain == domain,
                Certificate.vps_id == payload.vps_id,
            )
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
                vps_id=payload.vps_id,
                issuer=cert_data.get("issuer", "Let's Encrypt"),
                expiry=expiry,
                status=cert_data.get("status", "valid"),
            )
            db.add(cert)
        count += 1

    # Remove stale certs scoped to this VPS only — entries for domains
    # this agent no longer reports (vhost or cert was deleted on its host).
    stale_q = select(Certificate).where(Certificate.vps_id == payload.vps_id)
    if synced_domains:
        stale_q = stale_q.where(Certificate.domain.notin_(synced_domains))
    stale = await db.execute(stale_q)
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
            select(Domain).where(
                Domain.domain == domain_name,
                Domain.vps_id == payload.vps_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.container = d.get("container", existing.container)
            existing.port = d.get("port", existing.port)
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


# ---------------------------------------------------------------------------
# Hub→Agent execution channel (Phase C)
# ---------------------------------------------------------------------------


class ExecPayload(BaseModel):
    """Body of POST /agent/exec — enqueue a command for a remote VPS."""

    vps_id: str
    argv: list[str]  # e.g. ["cert", "health"] — `vsa` is prepended on the agent
    timeout_seconds: int = 120
    requested_by: str = ""


class CommandResultPayload(BaseModel):
    """Body of POST /agent/commands/{id}/result — agent reports back."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def _serialize_command(c: AgentCommand) -> dict[str, Any]:
    return {
        "id": c.id,
        "vps_id": c.vps_id,
        "argv": c.argv,
        "status": c.status,
        "timeout_seconds": c.timeout_seconds,
        "requested_by": c.requested_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "taken_at": c.taken_at.isoformat() if c.taken_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "exit_code": c.exit_code,
        "stdout": c.stdout,
        "stderr": c.stderr,
    }


@router.post("/agent/exec")
async def agent_exec_enqueue(
    payload: ExecPayload,
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a command for a target VPS.

    Auth comes from the surrounding nginx basic-auth — this endpoint is
    user-facing (called by `vsa fleet exec` running on the hub). Returns
    the queued row so the caller can poll for the result.
    """
    if not payload.argv:
        raise HTTPException(status_code=400, detail="argv must be non-empty")

    cmd = AgentCommand(
        vps_id=payload.vps_id,
        argv=payload.argv,
        timeout_seconds=payload.timeout_seconds,
        requested_by=payload.requested_by,
        status="pending",
    )
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    return _serialize_command(cmd)


@router.get("/agent/commands")
async def agent_list_commands(
    vps_id: str,
    status: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """List commands for a VPS — used by the agent to poll for pending work.

    Token-authenticated (the agent passes its VSA_AGENT_TOKEN). The agent
    typically calls this with ``status=pending`` and a small ``limit``.
    """
    q = select(AgentCommand).where(AgentCommand.vps_id == vps_id)
    if status:
        q = q.where(AgentCommand.status == status)
    q = q.order_by(AgentCommand.created_at).limit(limit)
    result = await db.execute(q)
    return [_serialize_command(c) for c in result.scalars().all()]


@router.get("/agent/commands/{command_id}")
async def agent_get_command(
    command_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single command by id — used by `vsa fleet exec` to poll.

    Unauthenticated (behind nginx basic-auth) so the hub-side CLI can poll
    without juggling agent tokens.
    """
    result = await db.execute(
        select(AgentCommand).where(AgentCommand.id == command_id)
    )
    cmd = result.scalar_one_or_none()
    if cmd is None:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    return _serialize_command(cmd)


@router.post("/agent/commands/{command_id}/take")
async def agent_take_command(
    command_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Mark a pending command as ``running`` and stamp ``taken_at``.

    Idempotent-ish: refuses to re-take a command that's already running or
    completed. Token-authenticated.
    """
    result = await db.execute(
        select(AgentCommand).where(AgentCommand.id == command_id)
    )
    cmd = result.scalar_one_or_none()
    if cmd is None:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    if cmd.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Command {command_id} is already {cmd.status}",
        )
    cmd.status = "running"
    cmd.taken_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(cmd)
    return _serialize_command(cmd)


@router.post("/agent/commands/{command_id}/result")
async def agent_command_result(
    command_id: int,
    payload: CommandResultPayload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
):
    """Receive the execution result from the agent."""
    result = await db.execute(
        select(AgentCommand).where(AgentCommand.id == command_id)
    )
    cmd = result.scalar_one_or_none()
    if cmd is None:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")
    cmd.status = "completed"
    cmd.completed_at = datetime.now(timezone.utc)
    cmd.exit_code = payload.exit_code
    cmd.stdout = payload.stdout
    cmd.stderr = payload.stderr
    await db.commit()
    await db.refresh(cmd)
    return _serialize_command(cmd)
