"""Agent sync service — collectors and HTTP client for hub communication."""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from vsa.config import get_config

console = Console()

# ---------------------------------------------------------------------------
# Sync state persistence
# ---------------------------------------------------------------------------

_STATE_PATH = Path("/var/lib/vsa/agent_sync_state.json")


def _load_sync_state() -> dict[str, Any]:
    if _STATE_PATH.exists():
        return json.loads(_STATE_PATH.read_text())
    return {"last_audit_id": 0, "file_offsets": {}}


def _save_sync_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Data collectors (pure-ish functions, testable)
# ---------------------------------------------------------------------------


def collect_heartbeat() -> dict[str, str]:
    """Return heartbeat payload with vps_id, hostname, and ip_address."""
    cfg = get_config()
    hostname = socket.gethostname()
    try:
        ip_address = socket.gethostbyname(hostname)
    except socket.gaierror:
        ip_address = ""
    return {
        "vps_id": cfg.vps_id,
        "hostname": hostname,
        "ip_address": ip_address,
    }


def collect_containers() -> list[dict[str, str]]:
    """List all Docker containers via ``docker ps -a``."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    containers: list[dict[str, str]] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        data = json.loads(line)
        # Labels come as a comma-separated string like
        # "com.docker.compose.project=foo,com.docker.compose.service=bar".
        compose_project = ""
        compose_service = ""
        for label in data.get("Labels", "").split(","):
            key, sep, value = label.partition("=")
            if not sep:
                continue
            key = key.strip()
            if key == "com.docker.compose.project":
                compose_project = value.strip()
            elif key == "com.docker.compose.service":
                compose_service = value.strip()
        containers.append(
            {
                "name": data.get("Names", ""),
                "image": data.get("Image", ""),
                "status": data.get("Status", ""),
                "ports": data.get("Ports", ""),
                "compose_project": compose_project,
                "compose_service": compose_service,
            }
        )
    return containers


def collect_certificates(letsencrypt_live_dir: Path) -> list[dict[str, str]]:
    """List certificate info by running openssl on the host filesystem.

    Iterates over ``<letsencrypt_live_dir>/<domain>/cert.pem`` and invokes
    ``openssl x509 -noout -enddate`` directly. The agent runs as root, which
    is required to traverse the Let's Encrypt ``live/`` directory (mode 0700).

    Was previously implemented via ``docker compose exec nginx openssl …``,
    but ``nginx:1.25-alpine`` does not ship openssl, so every cert ended up
    with an empty expiry. Reading from the host bind-mount avoids the dependency
    on whatever utilities happen to be in the proxy image.
    """
    if not letsencrypt_live_dir.is_dir():
        return []

    lines: list[str] = []
    for entry in sorted(letsencrypt_live_dir.iterdir()):
        if not entry.is_dir() or entry.name == "README":
            continue
        cert = entry / "cert.pem"
        if not cert.is_file():
            continue
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate", "-in", str(cert)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or "=" not in result.stdout:
            continue
        expiry = result.stdout.split("=", 1)[1].strip()
        lines.append(f"{entry.name}|{expiry}")
    return parse_cert_output("\n".join(lines))


def parse_cert_output(raw: str) -> list[dict[str, str]]:
    """Parse ``domain|expiry`` lines into structured dicts."""
    certs: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        if "|" not in line:
            continue
        domain, expiry_raw = line.split("|", 1)
        domain = domain.strip()
        expiry_raw = expiry_raw.strip()
        # Determine status
        status = "valid"
        try:
            expiry_dt = datetime.strptime(expiry_raw, "%b %d %H:%M:%S %Y %Z")
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            if expiry_dt < datetime.now(timezone.utc):
                status = "expired"
            expiry_iso = expiry_dt.isoformat()
        except (ValueError, TypeError):
            expiry_iso = expiry_raw
            status = "unknown"
        certs.append(
            {
                "domain": domain,
                "expiry": expiry_iso,
                "issuer": "Let's Encrypt",
                "status": status,
            }
        )
    return certs


_UPSTREAM_RE = re.compile(r"set\s+\$\w*upstream\s+(.+?):(\d+)\s*;")
# Multipoint pattern: `set $route_1 backend:port;` etc. used by app vhosts
# that route different paths to different containers. Take the first match
# as the "primary" backend for display purposes.
_ROUTE_RE = re.compile(r"set\s+\$\w+\s+([\w-]+):(\d+)\s*;")


def collect_domains(vhost_dir: Path) -> list[dict[str, Any]]:
    """Scan NGINX vhost .conf files and extract domain/container/port.

    Skips macOS AppleDouble resource forks (``._*``) and any file that
    fails UTF-8 decode — neither shape can possibly contain a valid
    nginx upstream directive. Without this, a single stray ``._*`` file
    (e.g. left behind by an rsync from macOS) would crash the agent and
    block the entire domains-sync.
    """
    domains: list[dict[str, Any]] = []
    if not vhost_dir.is_dir():
        return domains
    for conf in sorted(vhost_dir.glob("*.conf")):
        if conf.name.startswith("._"):
            continue
        try:
            content = conf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        match = _UPSTREAM_RE.search(content) or _ROUTE_RE.search(content)
        if match:
            container = match.group(1)
            port = int(match.group(2))
            domains.append(
                {
                    "domain": conf.stem,
                    "container": container,
                    "port": port,
                }
            )
    return domains


def collect_unsent_audit_events(
    db_path: Path, last_id: int
) -> tuple[list[dict[str, Any]], int]:
    """Read audit events from local SQLite that haven't been synced yet."""
    if not db_path.exists():
        return [], last_id

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE id > ? ORDER BY id LIMIT 500",
            (last_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return [], last_id
    finally:
        conn.close()

    events: list[dict[str, Any]] = []
    max_id = last_id
    for row in rows:
        event = dict(row)
        max_id = max(max_id, event["id"])
        events.append(event)
    return events, max_id


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _post(
    client: httpx.Client, url: str, payload: dict[str, Any]
) -> httpx.Response:
    """POST JSON to the hub with a 30s timeout."""
    return client.post(url, json=payload, timeout=30.0)


# ---------------------------------------------------------------------------
# Sync functions (collect → POST)
# ---------------------------------------------------------------------------


def sync_heartbeat(client: httpx.Client, hub_url: str) -> None:
    data = collect_heartbeat()
    resp = _post(client, f"{hub_url}/agent/heartbeat", data)
    resp.raise_for_status()


def sync_containers(client: httpx.Client, hub_url: str) -> None:
    cfg = get_config()
    containers = collect_containers()
    resp = _post(
        client,
        f"{hub_url}/agent/containers-sync",
        {"vps_id": cfg.vps_id, "containers": containers},
    )
    resp.raise_for_status()


def sync_certificates(client: httpx.Client, hub_url: str) -> None:
    cfg = get_config()
    certs = collect_certificates(cfg.letsencrypt_live_dir)
    resp = _post(
        client,
        f"{hub_url}/agent/certs-sync",
        {"vps_id": cfg.vps_id, "certs": certs},
    )
    resp.raise_for_status()


def sync_domains(client: httpx.Client, hub_url: str) -> None:
    cfg = get_config()
    # Read what is *actually deployed* (the bind-mount NGINX serves), not the
    # git checkout — those diverge whenever a VPS hosts only a subset of the
    # repo's vhosts (the warm-standby case). Falls back to the repo dir if
    # the mount doesn't exist yet (fresh VPS, before first `vsa vhost sync`).
    vhost_dir = (
        cfg.mount_vhost_dir if cfg.mount_vhost_dir.is_dir() else cfg.repo_vhost_dir
    )
    domains = collect_domains(vhost_dir)
    resp = _post(
        client,
        f"{hub_url}/agent/domains-sync",
        {"vps_id": cfg.vps_id, "domains": domains},
    )
    resp.raise_for_status()


def sync_audit_events(client: httpx.Client, hub_url: str) -> None:
    cfg = get_config()
    state = _load_sync_state()
    last_id = state.get("last_audit_id", 0)

    events, new_last_id = collect_unsent_audit_events(cfg.audit_db_path, last_id)
    if not events:
        return

    resp = _post(
        client, f"{hub_url}/agent/audit-sync", {"events": events}
    )
    resp.raise_for_status()

    state["last_audit_id"] = new_last_id
    _save_sync_state(state)


# ---------------------------------------------------------------------------
# Traffic stats collector
# ---------------------------------------------------------------------------

_DEFAULT_LOG_DIR = Path("/var/log/nginx/domains")


def collect_traffic_stats(
    log_dir: Path, file_offsets: dict[str, int]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse per-domain JSON access logs incrementally and aggregate stats.

    Returns a list of stat entries and updated file offsets.
    """
    stats: list[dict[str, Any]] = []
    new_offsets = dict(file_offsets)

    if not log_dir.is_dir():
        return stats, new_offsets

    for log_file in sorted(log_dir.glob("*.access.json")):
        domain = log_file.name.replace(".access.json", "")
        offset = file_offsets.get(log_file.name, 0)

        try:
            file_size = log_file.stat().st_size
        except OSError:
            continue

        if file_size <= offset:
            # File was truncated (rotated) — reset offset
            if file_size < offset:
                offset = 0
            else:
                continue

        requests = 0
        status_2xx = 0
        status_3xx = 0
        status_4xx = 0
        status_5xx = 0
        bytes_sent = 0
        total_request_time = 0.0
        period_start: str | None = None
        period_end: str | None = None

        try:
            with open(log_file, "r") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    requests += 1
                    status = entry.get("status", 0)
                    if isinstance(status, str):
                        try:
                            status = int(status)
                        except ValueError:
                            status = 0

                    if 200 <= status < 300:
                        status_2xx += 1
                    elif 300 <= status < 400:
                        status_3xx += 1
                    elif 400 <= status < 500:
                        status_4xx += 1
                    elif 500 <= status < 600:
                        status_5xx += 1

                    bs = entry.get("body_bytes_sent", 0)
                    if isinstance(bs, str):
                        try:
                            bs = int(bs)
                        except ValueError:
                            bs = 0
                    bytes_sent += bs

                    rt = entry.get("request_time", 0)
                    if isinstance(rt, str):
                        try:
                            rt = float(rt)
                        except ValueError:
                            rt = 0.0
                    total_request_time += rt

                    ts = entry.get("time", "")
                    if ts:
                        if period_start is None:
                            period_start = ts
                        period_end = ts

                new_offset = f.tell()
        except OSError:
            continue

        new_offsets[log_file.name] = new_offset

        if requests > 0:
            avg_request_time_ms = int((total_request_time / requests) * 1000)
            stats.append(
                {
                    "domain": domain,
                    "requests": requests,
                    "status_2xx": status_2xx,
                    "status_3xx": status_3xx,
                    "status_4xx": status_4xx,
                    "status_5xx": status_5xx,
                    "bytes_sent": bytes_sent,
                    "avg_request_time_ms": avg_request_time_ms,
                    "period_start": period_start or "",
                    "period_end": period_end or "",
                }
            )

    return stats, new_offsets


def sync_traffic_stats(client: httpx.Client, hub_url: str) -> None:
    cfg = get_config()
    state = _load_sync_state()
    file_offsets = state.get("file_offsets", {})

    stats, new_offsets = collect_traffic_stats(_DEFAULT_LOG_DIR, file_offsets)
    if not stats:
        state["file_offsets"] = new_offsets
        _save_sync_state(state)
        return

    resp = _post(
        client,
        f"{hub_url}/agent/traffic-sync",
        {"vps_id": cfg.vps_id, "stats": stats},
    )
    resp.raise_for_status()

    state["file_offsets"] = new_offsets
    _save_sync_state(state)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_STEPS: list[tuple[str, Any]] = [
    ("Heartbeat", sync_heartbeat),
    ("Containers", sync_containers),
    ("Certificates", sync_certificates),
    ("Domains", sync_domains),
    ("Audit events", sync_audit_events),
    ("Traffic stats", sync_traffic_stats),
]


def run_sync(hub_url: str, token: str) -> None:
    """Execute one full sync cycle against the hub."""
    client = httpx.Client(headers={"Authorization": f"Bearer {token}"})
    try:
        for label, fn in _STEPS:
            try:
                fn(client, hub_url)
                console.print(f"  [green]\u2713[/green] {label}")
            except Exception as exc:
                console.print(f"  [red]\u2717[/red] {label}: {exc}")
    finally:
        client.close()
