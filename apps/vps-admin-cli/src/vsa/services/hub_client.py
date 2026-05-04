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


# ---------------------------------------------------------------------------
# Hub→agent execution channel (Phase C)
# ---------------------------------------------------------------------------


def enqueue_command(
    *,
    vps_id: str,
    argv: list[str],
    timeout_seconds: int = 120,
    requested_by: str = "",
) -> dict[str, Any]:
    """POST /agent/exec — enqueue a command for a target VPS."""
    with _client() as client:
        resp = client.post(
            "/agent/exec",
            json={
                "vps_id": vps_id,
                "argv": argv,
                "timeout_seconds": timeout_seconds,
                "requested_by": requested_by,
            },
        )
        _check(resp)
        return resp.json()


def get_command(command_id: int) -> dict[str, Any]:
    """GET /agent/commands/{id} — fetch a command by id."""
    with _client() as client:
        resp = client.get(f"/agent/commands/{command_id}")
        _check(resp)
        return resp.json()


# ---------------------------------------------------------------------------
# Agent-side helpers (used by the agent loop, not by user commands)
#
# These need a different auth setup — the agent uses VSA_AGENT_TOKEN, not
# the basic-auth user/pass. We accept the token explicitly since `_client()`
# above is geared for the user-facing flows.
# ---------------------------------------------------------------------------


def _agent_client(hub_url: str, agent_token: str) -> httpx.Client:
    return httpx.Client(
        base_url=hub_url,
        headers={"Authorization": f"Bearer {agent_token}"},
        timeout=30.0,
    )


def list_pending_for_vps(
    *, hub_url: str, agent_token: str, vps_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    with _agent_client(hub_url, agent_token) as client:
        resp = client.get(
            "/agent/commands",
            params={"vps_id": vps_id, "status": "pending", "limit": limit},
        )
        _check(resp)
        return resp.json()


def take_command(*, hub_url: str, agent_token: str, command_id: int) -> dict[str, Any]:
    with _agent_client(hub_url, agent_token) as client:
        resp = client.post(f"/agent/commands/{command_id}/take")
        _check(resp)
        return resp.json()


def post_command_result(
    *,
    hub_url: str,
    agent_token: str,
    command_id: int,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    with _agent_client(hub_url, agent_token) as client:
        resp = client.post(
            f"/agent/commands/{command_id}/result",
            json={"exit_code": exit_code, "stdout": stdout, "stderr": stderr},
        )
        _check(resp)
        return resp.json()
