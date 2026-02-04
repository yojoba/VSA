"""Traffic stats and log endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from vsa_api.services.loki import query_logs, query_traffic_stats

router = APIRouter(tags=["traffic"])


@router.get("/traffic/stats")
async def get_traffic_stats(
    domain: str | None = Query(None),
    period: str = Query("24h"),
):
    """Get aggregated traffic stats from Loki."""
    return await query_traffic_stats(period=period, domain=domain)


@router.get("/traffic/logs")
async def get_traffic_logs(
    domain: str = Query(...),
    limit: int = Query(100, ge=1, le=1000),
    since: str = Query("1h"),
):
    """Get raw traffic logs from Loki."""
    entries = await query_logs(domain=domain, limit=limit, since=since)
    return entries
