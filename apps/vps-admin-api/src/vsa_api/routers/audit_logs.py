"""Audit log endpoints — paginated, filterable audit trail.

Reads directly from the local SQLite audit DB (the CLI's source of truth)
for hub-local events, and merges with PostgreSQL for agent-synced remote events.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import AuditLog

router = APIRouter(tags=["audit"])

_LOCAL_AUDIT_DB = Path("/var/lib/vsa/audit.db")


def _read_local_sqlite(
    *,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    result: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read audit events from the CLI's local SQLite database."""
    if not _LOCAL_AUDIT_DB.exists():
        return []

    conn = sqlite3.connect(str(_LOCAL_AUDIT_DB))
    conn.row_factory = sqlite3.Row
    try:
        clauses: list[str] = []
        params: list[Any] = []

        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if target:
            clauses.append("target LIKE ?")
            params.append(f"%{target}%")
        if result:
            clauses.append("result = ?")
            params.append(result)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat())

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM audit_logs{where} ORDER BY timestamp DESC"

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _merge_events(
    local: list[dict[str, Any]],
    remote: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge local SQLite and remote PostgreSQL events, deduplicated by
    (timestamp, actor, action, target) and sorted newest-first."""
    seen: set[tuple[str, str, str, str]] = set()
    merged: list[dict[str, Any]] = []

    for event in local:
        key = (
            str(event.get("timestamp", "")),
            str(event.get("actor", "")),
            str(event.get("action", "")),
            str(event.get("target", "")),
        )
        if key not in seen:
            seen.add(key)
            merged.append(event)

    for event in remote:
        key = (
            str(event.get("timestamp", "")),
            str(event.get("actor", "")),
            str(event.get("action", "")),
            str(event.get("target", "")),
        )
        if key not in seen:
            seen.add(key)
            merged.append(event)

    merged.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return merged


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
    """List audit logs with filtering and pagination.

    Merges local SQLite (hub CLI events) with PostgreSQL (agent-synced remote events).
    """
    # 1. Read from local SQLite (hub-local CLI events)
    local_events = _read_local_sqlite(
        actor=actor, action=action, target=target,
        result=result, since=since, until=until,
    )

    # 2. Read from PostgreSQL (agent-synced remote events)
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

    rows = (await db.execute(query)).scalars().all()
    remote_events = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "vps_id": r.vps_id,
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "params": r.params or "{}",
            "result": r.result,
            "error": r.error,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]

    # 3. Merge and deduplicate
    all_events = _merge_events(local_events, remote_events)
    total = len(all_events)

    # 4. Paginate
    offset = (page - 1) * per_page
    page_events = all_events[offset : offset + per_page]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": e.get("id"),
                "timestamp": e.get("timestamp"),
                "vps_id": e.get("vps_id", ""),
                "actor": e.get("actor", ""),
                "action": e.get("action", ""),
                "target": e.get("target", ""),
                "params": json.loads(e["params"]) if isinstance(e.get("params"), str) else e.get("params", {}),
                "result": e.get("result", ""),
                "error": e.get("error"),
                "duration_ms": e.get("duration_ms"),
            }
            for e in page_events
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
    # Merge local + remote
    local_events = _read_local_sqlite(actor=actor, action=action, since=since, until=until)

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
    remote_events = [
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

    all_events = _merge_events(local_events, remote_events)

    if format == "json":
        data = [
            {
                "timestamp": e.get("timestamp"),
                "vps_id": e.get("vps_id", ""),
                "actor": e.get("actor", ""),
                "action": e.get("action", ""),
                "target": e.get("target", ""),
                "result": e.get("result", ""),
                "error": e.get("error"),
                "duration_ms": e.get("duration_ms"),
            }
            for e in all_events
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
    for e in all_events:
        writer.writerow([
            e.get("timestamp", ""),
            e.get("vps_id", ""), e.get("actor", ""), e.get("action", ""),
            e.get("target", ""), e.get("result", ""),
            e.get("error") or "", e.get("duration_ms") or "",
        ])

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )
