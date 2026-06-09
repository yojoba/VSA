"""Fleet alerting — collect problems and email a digest.

Self-contained, env-driven config (read from ``/etc/vsa/alert.env`` via the
systemd unit, or the shell). Covers two problem families:

* **certs / drift** — every finding from the hub's ``/fleet/drift`` report.
* **systems** — agents that stopped reporting (stale ``last_seen``) and
  containers that are down or unhealthy.

A small state file (``alert-state.json``) records the set of currently-firing
problems so we only email on *changes* (new/escalated problems, or a
recovery) instead of every run — a mini Alertmanager.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

import httpx

from vsa.services import hub_client

_LEVELS = {"info": 0, "warning": 1, "critical": 2}
_LEVEL_EMOJI = {"info": "🔵", "warning": "🟡", "critical": "🔴"}
_DEFAULT_STATE_PATH = Path("/var/lib/vsa/alert-state.json")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class AlertConfig:
    smtp_host: str = "mail.infomaniak.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    recipients: list[str] = field(default_factory=list)
    min_level: str = "warning"
    agent_stale_minutes: int = 10
    ignore_containers: list[str] = field(default_factory=list)
    state_path: Path = _DEFAULT_STATE_PATH
    subject_prefix: str = "[VSA]"
    # Disk-usage alarms (queried from the hub's Prometheus, which holds
    # node_filesystem metrics for every VPS via remote-write).
    prometheus_url: str = "http://localhost:9090"
    disk_warn_percent: float = 85.0
    disk_crit_percent: float = 92.0
    disk_mounts: str = "/|/var/lib/docker"  # PromQL regex of mountpoints to watch

    @classmethod
    def from_env(cls) -> "AlertConfig":
        def _split(val: str) -> list[str]:
            return [p.strip() for p in val.replace(";", ",").split(",") if p.strip()]

        user = os.environ.get("VSA_ALERT_SMTP_USER", "")
        return cls(
            smtp_host=os.environ.get("VSA_ALERT_SMTP_HOST", "mail.infomaniak.com"),
            smtp_port=int(os.environ.get("VSA_ALERT_SMTP_PORT", "587")),
            smtp_user=user,
            smtp_password=os.environ.get("VSA_ALERT_SMTP_PASSWORD", ""),
            mail_from=os.environ.get("VSA_ALERT_FROM", user),
            recipients=_split(os.environ.get("VSA_ALERT_TO", "")),
            min_level=os.environ.get("VSA_ALERT_MIN_LEVEL", "warning").lower(),
            agent_stale_minutes=int(os.environ.get("VSA_ALERT_AGENT_STALE_MINUTES", "10")),
            ignore_containers=_split(os.environ.get("VSA_ALERT_IGNORE_CONTAINERS", "")),
            state_path=Path(os.environ.get("VSA_ALERT_STATE_PATH", str(_DEFAULT_STATE_PATH))),
            subject_prefix=os.environ.get("VSA_ALERT_SUBJECT_PREFIX", "[VSA]"),
            prometheus_url=os.environ.get("VSA_ALERT_PROMETHEUS_URL", "http://localhost:9090"),
            disk_warn_percent=float(os.environ.get("VSA_ALERT_DISK_WARN_PERCENT", "85")),
            disk_crit_percent=float(os.environ.get("VSA_ALERT_DISK_CRIT_PERCENT", "92")),
            disk_mounts=os.environ.get("VSA_ALERT_DISK_MOUNTS", "/|/var/lib/docker"),
        )

    def validate(self) -> list[str]:
        errs = []
        if not self.smtp_user or not self.smtp_password:
            errs.append("VSA_ALERT_SMTP_USER / VSA_ALERT_SMTP_PASSWORD are required")
        if not self.mail_from:
            errs.append("VSA_ALERT_FROM (or VSA_ALERT_SMTP_USER) is required")
        if not self.recipients:
            errs.append("VSA_ALERT_TO must list at least one recipient")
        if self.min_level not in _LEVELS:
            errs.append(f"VSA_ALERT_MIN_LEVEL must be one of {sorted(_LEVELS)}")
        return errs


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Problem:
    level: str       # critical | warning | info
    category: str    # cert | drift | agent | container
    vps: str
    target: str      # domain / vps_id / container name
    detail: str

    @property
    def key(self) -> str:
        return f"{self.category}|{self.vps}|{self.target}|{self.level}"

    def line(self) -> str:
        emoji = _LEVEL_EMOJI.get(self.level, "•")
        return f"{emoji} [{self.level.upper()}] {self.category}: {self.target} ({self.vps}) — {self.detail}"


def _age_minutes(iso_ts: str, *, now: datetime) -> float | None:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 60.0


def problems_from_drift(report: dict[str, Any]) -> list[Problem]:
    out: list[Problem] = []
    for f in report.get("findings", []):
        kind = f.get("kind", "")
        category = "cert" if "cert" in kind else "drift"
        out.append(
            Problem(
                level=f.get("level", "warning"),
                category=category,
                vps=f.get("vps_id") or f.get("vps") or "—",
                target=f.get("domain") or kind,
                detail=f"{kind}: {f.get('detail', '')}".strip(": "),
            )
        )
    return out


def problems_from_vps(vps_nodes: list[dict[str, Any]], *, stale_minutes: int, now: datetime) -> list[Problem]:
    out: list[Problem] = []
    for v in vps_nodes:
        vid = v.get("vps_id", "?")
        age = _age_minutes(v.get("last_seen", ""), now=now)
        if age is None:
            out.append(Problem("warning", "agent", vid, vid, "agent never reported (no last_seen)"))
        elif age > stale_minutes:
            out.append(
                Problem(
                    "critical", "agent", vid, vid,
                    f"agent not reporting — last seen {int(age)} min ago (threshold {stale_minutes})",
                )
            )
    return out


def _container_problem(c: dict[str, Any], ignore: list[str]) -> Problem | None:
    name = c.get("name", "?")
    if any(pat and pat in name for pat in ignore):
        return None
    status = c.get("status", "") or ""
    vps = c.get("vps_id", "—")
    s = status.lower()
    # Down states (real crashes). Clean one-shot exits (Exited (0)) and the
    # transient "Created" state are NOT alerted — they're normal for agents.
    if s.startswith(("dead", "restarting")) or (s.startswith("exited") and "exited (0)" not in s):
        return Problem("critical", "container", vps, name, f"container down: {status}")
    if "(unhealthy)" in s:
        return Problem("warning", "container", vps, name, f"container unhealthy: {status}")
    return None


def problems_from_containers(containers: list[dict[str, Any]], *, ignore: list[str]) -> list[Problem]:
    out: list[Problem] = []
    for c in containers:
        p = _container_problem(c, ignore)
        if p is not None:
            out.append(p)
    return out


def problems_from_disk(
    prometheus_url: str,
    *,
    warn_percent: float,
    crit_percent: float,
    mounts: str,
    timeout: float = 10.0,
) -> list[Problem]:
    """Disk-usage alarms per VPS/mountpoint, queried from Prometheus.

    Reads node_filesystem_* for the watched mountpoints (all VPS, since vps-02/03
    remote-write their node-exporter metrics). Emits a warning at ``warn_percent``
    and a critical at ``crit_percent``. If Prometheus is unreachable, returns an
    empty list rather than blocking the rest of the alert run — a down Prometheus
    is itself caught by the container-down check.
    """
    expr = (
        '100 * (1 - '
        f'node_filesystem_avail_bytes{{fstype="ext4",mountpoint=~"{mounts}"}} / '
        f'node_filesystem_size_bytes{{fstype="ext4",mountpoint=~"{mounts}"}})'
    )
    try:
        resp = httpx.get(
            f"{prometheus_url.rstrip('/')}/api/v1/query",
            params={"query": expr},
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
    except (httpx.HTTPError, ValueError):
        return []

    out: list[Problem] = []
    for series in result:
        metric = series.get("metric", {})
        vps = metric.get("vps_id", "—")
        mount = metric.get("mountpoint", "?")
        try:
            pct = float(series["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if pct >= crit_percent:
            out.append(Problem(
                "critical", "disk", vps, mount,
                f"disk {mount} {pct:.0f}% full (threshold {crit_percent:.0f}%)",
            ))
        elif pct >= warn_percent:
            out.append(Problem(
                "warning", "disk", vps, mount,
                f"disk {mount} {pct:.0f}% full (threshold {warn_percent:.0f}%)",
            ))
    return out


def collect_problems(cfg: AlertConfig, *, now: datetime | None = None) -> list[Problem]:
    """Query the hub and return all problems at/above ``cfg.min_level``."""
    now = now or datetime.now(timezone.utc)
    problems: list[Problem] = []
    problems += problems_from_drift(hub_client.fleet_drift())
    problems += problems_from_vps(
        hub_client.list_vps(), stale_minutes=cfg.agent_stale_minutes, now=now
    )
    problems += problems_from_containers(
        hub_client.list_containers(), ignore=cfg.ignore_containers
    )
    problems += problems_from_disk(
        cfg.prometheus_url,
        warn_percent=cfg.disk_warn_percent,
        crit_percent=cfg.disk_crit_percent,
        mounts=cfg.disk_mounts,
    )
    threshold = _LEVELS.get(cfg.min_level, 1)
    kept = [p for p in problems if _LEVELS.get(p.level, 0) >= threshold]
    kept.sort(key=lambda p: (-_LEVELS.get(p.level, 0), p.category, p.target))
    return kept


# ---------------------------------------------------------------------------
# State (dedup so we only email on change)
# ---------------------------------------------------------------------------


def load_state(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text())
        return set(data.get("active", []))
    except (OSError, ValueError):
        return set()


def save_state(path: Path, problems: list[Problem], *, now: datetime) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"updated": now.isoformat(), "active": sorted(p.key for p in problems)},
                indent=2,
            )
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Email rendering + send
# ---------------------------------------------------------------------------


def _by_level(problems: list[Problem]) -> dict[str, list[Problem]]:
    grouped: dict[str, list[Problem]] = {"critical": [], "warning": [], "info": []}
    for p in problems:
        grouped.setdefault(p.level, []).append(p)
    return grouped


def render_subject(cfg: AlertConfig, current: list[Problem], resolved: bool) -> str:
    if resolved:
        return f"{cfg.subject_prefix} ✅ Tout est revenu à la normale"
    g = _by_level(current)
    nc, nw = len(g["critical"]), len(g["warning"])
    bits = []
    if nc:
        bits.append(f"{nc} critique{'s' if nc > 1 else ''}")
    if nw:
        bits.append(f"{nw} warning{'s' if nw > 1 else ''}")
    summary = ", ".join(bits) or f"{len(current)} problème(s)"
    return f"{cfg.subject_prefix} 🔴 {summary}"


def render_bodies(
    cfg: AlertConfig,
    current: list[Problem],
    new: list[Problem],
    resolved_keys: set[str],
    *,
    now: datetime,
) -> tuple[str, str]:
    """Return (plain_text, html)."""
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    new_keys = {p.key for p in new}

    # --- plain text ---
    lines = [f"VSA fleet alert — {ts}", ""]
    if not current:
        lines.append("✅ Tous les problèmes précédents sont résolus.")
    else:
        lines.append(f"{len(current)} problème(s) actif(s) (seuil: {cfg.min_level}):")
        lines.append("")
        for p in current:
            tag = "  >>> NOUVEAU " if p.key in new_keys else "      "
            lines.append(f"{tag}{p.line()}")
    if resolved_keys:
        lines += ["", f"Résolus depuis la dernière alerte: {len(resolved_keys)}"]
        for k in sorted(resolved_keys):
            lines.append(f"      ✅ {k}")
    lines += ["", "—", "dashboard.flowbiz.ai/health  ·  alerte automatique VSA"]
    text = "\n".join(lines)

    # --- html ---
    def row(p: Problem) -> str:
        color = {"critical": "#c0392b", "warning": "#d68910", "info": "#2471a3"}.get(p.level, "#555")
        badge = "NOUVEAU" if p.key in new_keys else ""
        badge_html = f'<span style="background:#c0392b;color:#fff;border-radius:3px;padding:1px 6px;font-size:11px;margin-left:6px">{badge}</span>' if badge else ""
        return (
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee">'
            f'<b style="color:{color}">{p.level.upper()}</b>{badge_html}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.category}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee"><code>{p.target}</code></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.vps}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #eee">{p.detail}</td></tr>'
        )

    if current:
        table = (
            '<table style="border-collapse:collapse;font-family:system-ui,sans-serif;font-size:14px;width:100%">'
            '<tr style="text-align:left;color:#888;font-size:12px">'
            "<th style='padding:6px 10px'>Niveau</th><th style='padding:6px 10px'>Type</th>"
            "<th style='padding:6px 10px'>Cible</th><th style='padding:6px 10px'>VPS</th>"
            "<th style='padding:6px 10px'>Détail</th></tr>"
            + "".join(row(p) for p in current)
            + "</table>"
        )
        header = f"<h2 style='font-family:system-ui,sans-serif'>🔴 {len(current)} problème(s) actif(s)</h2>"
    else:
        table = ""
        header = "<h2 style='font-family:system-ui,sans-serif;color:#27ae60'>✅ Tout est revenu à la normale</h2>"

    resolved_html = ""
    if resolved_keys:
        items = "".join(f"<li><code>{k}</code></li>" for k in sorted(resolved_keys))
        resolved_html = f"<p style='color:#27ae60'>Résolus: {len(resolved_keys)}</p><ul>{items}</ul>"

    html = (
        f"<div style='font-family:system-ui,sans-serif;color:#222'>{header}"
        f"<p style='color:#888;font-size:12px'>{ts} · seuil <b>{cfg.min_level}</b></p>"
        f"{table}{resolved_html}"
        "<p style='color:#888;font-size:12px;margin-top:18px'>"
        "<a href='https://dashboard.flowbiz.ai/health'>dashboard.flowbiz.ai/health</a> · alerte automatique VSA</p></div>"
    )
    return text, html


def send_email(cfg: AlertConfig, subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = ", ".join(cfg.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(cfg.smtp_user, cfg.smtp_password)
        server.send_message(msg)
