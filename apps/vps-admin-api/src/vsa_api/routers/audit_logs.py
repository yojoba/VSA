"""Audit log endpoints — paginated, filterable audit trail."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import AuditLog

router = APIRouter(tags=["audit"])


@router.get("/audit-logs")
async def list_audit_logs(
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    target: Optional[str] = Query(None),
    result: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List audit logs with filtering and pagination."""
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())

    if actor:
        query = query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action == action)
    if target:
        query = query.where(AuditLog.target.contains(target))
    if result:
        query = query.where(AuditLog.result == result)
    if since:
        query = query.where(AuditLog.timestamp >= since)
    if until:
        query = query.where(AuditLog.timestamp <= until)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)
    rows = (await db.execute(query)).scalars().all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "vps_id": r.vps_id,
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "params": json.loads(r.params) if r.params else {},
                "result": r.result,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ],
    }


@router.get("/audit-logs/export")
async def export_audit_logs(
    format: str = Query("csv", regex="^(csv|json)$"),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Export audit logs as CSV or JSON for ISO compliance."""
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())

    if actor:
        query = query.where(AuditLog.actor == actor)
    if action:
        query = query.where(AuditLog.action == action)
    if since:
        query = query.where(AuditLog.timestamp >= since)
    if until:
        query = query.where(AuditLog.timestamp <= until)

    rows = (await db.execute(query)).scalars().all()

    if format == "json":
        data = [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "vps_id": r.vps_id,
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "result": r.result,
                "error": r.error,
                "duration_ms": r.duration_ms,
            }
            for r in rows
        ]
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
        )

    # CSV export
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp", "vps_id", "actor", "action", "target", "result", "error", "duration_ms"])
    for r in rows:
        writer.writerow([
            r.timestamp.isoformat() if r.timestamp else "",
            r.vps_id, r.actor, r.action, r.target, r.result,
            r.error or "", r.duration_ms or "",
        ])

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )
