"""Thin HTTP client for the VSA hub API (`vsa fleet …` commands)."""

from __future__ import annotations

from typing import Any

import httpx

from vsa.config import get_config
from vsa.errors import VsaError


class HubClientError(VsaError):
    """Raised when a hub API call fails."""


def _client() -> httpx.Client:
    cfg = get_config()
    if not cfg.hub_url:
        raise HubClientError(
            "VSA_HUB_URL is not set. `vsa fleet …` commands need to know "
            "where the dashboard API lives. Set it in /etc/vsa/agent.env "
            "(or your shell), e.g. VSA_HUB_URL=https://dashboard.flowbiz.ai/api"
        )
    auth: tuple[str, str] | None = None
    if cfg.hub_auth and ":" in cfg.hub_auth:
        user, _, password = cfg.hub_auth.partition(":")
        auth = (user, password)
    return httpx.Client(base_url=cfg.hub_url, auth=auth, timeout=30.0)


def _check(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    detail = ""
    try:
        detail = resp.json().get("detail", "")
    except Exception:
        detail = resp.text[:300]
    raise HubClientError(f"{resp.request.method} {resp.request.url} → {resp.status_code}: {detail}")


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


def list_assignments() -> list[dict[str, Any]]:
    with _client() as client:
        resp = client.get("/assignments")
        _check(resp)
        return resp.json()


def get_assignment(domain: str) -> dict[str, Any] | None:
    with _client() as client:
        resp = client.get(f"/assignments/{domain}")
        if resp.status_code == 404:
            return None
        _check(resp)
        return resp.json()


def upsert_assignment(
    domain: str,
    *,
    primary_vps_id: str,
    standby_vps_ids: list[str],
    notes: str = "",
) -> dict[str, Any]:
    payload = {
        "primary_vps_id": primary_vps_id,
        "standby_vps_ids": standby_vps_ids,
        "notes": notes,
    }
    with _client() as client:
        resp = client.put(f"/assignments/{domain}", json=payload)
        _check(resp)
        return resp.json()


def delete_assignment(domain: str) -> None:
    with _client() as client:
        resp = client.delete(f"/assignments/{domain}")
        _check(resp)


# ---------------------------------------------------------------------------
# Domains (read-only, used by `vsa fleet backfill`)
# ---------------------------------------------------------------------------


def list_domains() -> list[dict[str, Any]]:
    with _client() as client:
        resp = client.get("/domains")
        _check(resp)
        return resp.json()
