"""Loki query client for raw traffic log retrieval."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from vsa_api.config import settings

log = logging.getLogger(__name__)

_DURATION_MAP = {
    "1h": "1h",
    "6h": "6h",
    "24h": "24h",
    "7d": "168h",
}


async def query_logs(
    domain: str, limit: int = 100, since: str = "1h"
) -> list[dict[str, Any]]:
    """Query Loki for raw log entries for a domain."""
    query = f'{{job="nginx-domain-access", domain="{domain}"}}'
    loki_since = _DURATION_MAP.get(since, since)
    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "since": loki_since,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.loki_url}/loki/api/v1/query_range",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    entries: list[dict[str, Any]] = []
    results = data.get("data", {}).get("result", [])
    for stream in results:
        for ts, line in stream.get("values", []):
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                parsed = {"raw": line}
            entries.append(parsed)

    # Sort by time descending (most recent first)
    entries.sort(key=lambda e: e.get("time", ""), reverse=True)
    return entries[:limit]


async def _instant_query(
    client: httpx.AsyncClient, query: str
) -> dict[str, float]:
    """Run a LogQL instant metric query, return {domain: value}."""
    try:
        resp = await client.get(
            f"{settings.loki_url}/loki/api/v1/query",
            params={"query": query},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Loki metric query failed: %s — %s", query[:80], exc)
        return {}

    result: dict[str, float] = {}
    for entry in data.get("data", {}).get("result", []):
        domain = entry.get("metric", {}).get("domain", "")
        if not domain:
            continue
        value = float(entry.get("value", [0, "0"])[1])
        result[domain] = value
    return result


async def query_traffic_stats(
    period: str = "24h", domain: str | None = None
) -> list[dict[str, Any]]:
    """Query Loki for aggregated traffic stats per domain using LogQL metrics."""
    duration = _DURATION_MAP.get(period, period)
    domain_filter = f', domain="{domain}"' if domain else ""
    sel = f'{{job="nginx-domain-access"{domain_filter}}}'

    queries = {
        "requests": f"sum by (domain) (count_over_time({sel}[{duration}]))",
        "status_2xx": f'sum by (domain) (count_over_time({{job="nginx-domain-access"{domain_filter}, status=~"2.."}}[{duration}]))',
        "status_3xx": f'sum by (domain) (count_over_time({{job="nginx-domain-access"{domain_filter}, status=~"3.."}}[{duration}]))',
        "status_4xx": f'sum by (domain) (count_over_time({{job="nginx-domain-access"{domain_filter}, status=~"4.."}}[{duration}]))',
        "status_5xx": f'sum by (domain) (count_over_time({{job="nginx-domain-access"{domain_filter}, status=~"5.."}}[{duration}]))',
        "bytes_sent": f"sum by (domain) (sum_over_time({sel} | json | unwrap body_bytes_sent [{duration}]))",
        "avg_request_time": f"avg by (domain) (avg_over_time({sel} | json | unwrap request_time [{duration}]))",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        keys = list(queries.keys())
        responses = await asyncio.gather(
            *(_instant_query(client, queries[k]) for k in keys)
        )

    parsed: dict[str, dict[str, float]] = dict(zip(keys, responses))

    # Collect all domains from all query results
    all_domains: set[str] = set()
    for v in parsed.values():
        all_domains.update(v.keys())

    stats = []
    for d in all_domains:
        avg_s = parsed.get("avg_request_time", {}).get(d, 0.0)
        stats.append(
            {
                "domain": d,
                "requests": int(parsed.get("requests", {}).get(d, 0)),
                "status_2xx": int(parsed.get("status_2xx", {}).get(d, 0)),
                "status_3xx": int(parsed.get("status_3xx", {}).get(d, 0)),
                "status_4xx": int(parsed.get("status_4xx", {}).get(d, 0)),
                "status_5xx": int(parsed.get("status_5xx", {}).get(d, 0)),
                "bytes_sent": int(parsed.get("bytes_sent", {}).get(d, 0)),
                "avg_request_time_ms": int(avg_s * 1000),
            }
        )

    stats.sort(key=lambda x: x["requests"], reverse=True)
    return stats
