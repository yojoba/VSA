"""Tests for the fleet alerting logic (collection, filtering, state diff)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from vsa.services import alerting
from vsa.services.alerting import (
    AlertConfig,
    Problem,
    load_state,
    problems_from_blackbox,
    problems_from_containers,
    problems_from_disk,
    problems_from_drift,
    problems_from_vps,
    save_state,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


# --- drift ---------------------------------------------------------------


def test_drift_findings_become_problems_with_category():
    report = {
        "findings": [
            {"level": "critical", "kind": "cert-expired", "domain": "a.ch", "vps_id": "vps-02", "detail": "expired"},
            {"level": "warning", "kind": "rogue-host", "domain": "b.ch", "vps_id": "vps-01", "detail": "unexpected"},
        ]
    }
    ps = problems_from_drift(report)
    assert {p.category for p in ps} == {"cert", "drift"}
    cert = next(p for p in ps if p.category == "cert")
    assert cert.level == "critical" and cert.target == "a.ch" and cert.vps == "vps-02"


# --- agents --------------------------------------------------------------


def test_stale_agent_is_critical_fresh_is_ok():
    nodes = [
        {"vps_id": "vps-01", "last_seen": _iso(1)},     # fresh
        {"vps_id": "vps-02", "last_seen": _iso(45)},    # stale
        {"vps_id": "vps-03", "last_seen": ""},          # never
    ]
    ps = problems_from_vps(nodes, stale_minutes=10, now=NOW)
    by = {p.vps: p for p in ps}
    assert "vps-01" not in by
    assert by["vps-02"].level == "critical"
    assert by["vps-03"].level == "warning"


# --- containers ----------------------------------------------------------


def test_container_states():
    containers = [
        {"vps_id": "v1", "name": "ok", "status": "Up 3 months (healthy)"},
        {"vps_id": "v1", "name": "sick", "status": "Up 2 months (unhealthy)"},
        {"vps_id": "v1", "name": "crashed", "status": "Exited (1) 2 hours ago"},
        {"vps_id": "v1", "name": "oneshot", "status": "Exited (0) 7 days ago"},
        {"vps_id": "v1", "name": "boot", "status": "Created"},
    ]
    ps = problems_from_containers(containers, ignore=[])
    by = {p.target: p for p in ps}
    assert "ok" not in by and "oneshot" not in by and "boot" not in by
    assert by["sick"].level == "warning"
    assert by["crashed"].level == "critical"


def test_container_ignore_list():
    containers = [{"vps_id": "v1", "name": "reverse-proxy-nginx", "status": "Up (unhealthy)"}]
    assert problems_from_containers(containers, ignore=["reverse-proxy-nginx"]) == []


# --- min_level filtering -------------------------------------------------


def test_collect_filters_below_min_level(monkeypatch):
    monkeypatch.setattr(alerting.hub_client, "fleet_drift", lambda: {
        "findings": [
            {"level": "info", "kind": "domain-without-assignment", "domain": "x.ch", "vps_id": "vps-01", "detail": ""},
            {"level": "critical", "kind": "cert-expired", "domain": "y.ch", "vps_id": "vps-02", "detail": "boom"},
        ]
    })
    monkeypatch.setattr(alerting.hub_client, "list_vps", lambda: [])
    monkeypatch.setattr(alerting.hub_client, "list_containers", lambda: [])
    monkeypatch.setattr(alerting.httpx, "get", lambda *a, **k: _FakeResp(_disk_payload()))

    cfg = AlertConfig(min_level="warning")
    ps = alerting.collect_problems(cfg, now=NOW)
    assert len(ps) == 1 and ps[0].level == "critical"

    cfg_info = AlertConfig(min_level="info")
    assert len(alerting.collect_problems(cfg_info, now=NOW)) == 2


def test_collect_sorts_critical_first(monkeypatch):
    monkeypatch.setattr(alerting.hub_client, "fleet_drift", lambda: {
        "findings": [
            {"level": "warning", "kind": "cert-expiring-soon", "domain": "w.ch", "vps_id": "v", "detail": ""},
            {"level": "critical", "kind": "cert-expired", "domain": "c.ch", "vps_id": "v", "detail": ""},
        ]
    })
    monkeypatch.setattr(alerting.hub_client, "list_vps", lambda: [])
    monkeypatch.setattr(alerting.hub_client, "list_containers", lambda: [])
    monkeypatch.setattr(alerting.httpx, "get", lambda *a, **k: _FakeResp(_disk_payload()))
    ps = alerting.collect_problems(AlertConfig(min_level="info"), now=NOW)
    assert ps[0].level == "critical"


# --- disk ----------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _disk_payload(*samples):
    return {"data": {"result": [
        {"metric": {"vps_id": v, "mountpoint": m, "fstype": "ext4"}, "value": [0, str(p)]}
        for v, m, p in samples
    ]}}


def test_disk_thresholds(monkeypatch):
    monkeypatch.setattr(alerting.httpx, "get", lambda *a, **k: _FakeResp(
        _disk_payload(("vps-01", "/", 93.0), ("vps-02", "/", 86.0), ("vps-03", "/", 40.0))
    ))
    ps = problems_from_disk("http://x", warn_percent=85, crit_percent=92, mounts="/")
    by = {p.vps: p for p in ps}
    assert by["vps-01"].level == "critical"
    assert by["vps-02"].level == "warning"
    assert "vps-03" not in by  # below warn threshold → no problem
    assert by["vps-01"].category == "disk" and by["vps-01"].target == "/"


def test_disk_prometheus_unreachable_is_empty(monkeypatch):
    def boom(*a, **k):
        raise alerting.httpx.ConnectError("refused")

    monkeypatch.setattr(alerting.httpx, "get", boom)
    assert problems_from_disk("http://x", warn_percent=85, crit_percent=92, mounts="/") == []


# --- blackbox (external synthetic probes) --------------------------------


def _blackbox_fake(*, probe_samples, cert_samples):
    """Fake httpx.get that branches on the PromQL query — probes vs cert."""
    def _resp(*a, **k):
        q = k.get("params", {}).get("query", "")
        if "probe_success" in q:
            samples = [(i, str(v)) for i, v in probe_samples]
        elif "probe_ssl_earliest_cert_expiry" in q:
            samples = [(i, str(v)) for i, v in cert_samples]
        else:
            samples = []
        return _FakeResp({"data": {"result": [
            {"metric": {"instance": i, "vps_id": "ext", "job": "blackbox"}, "value": [0, v]}
            for i, v in samples
        ]}})
    return _resp


def test_blackbox_down_and_cert_thresholds(monkeypatch):
    monkeypatch.setattr(alerting.httpx, "get", _blackbox_fake(
        probe_samples=[
            ("https://app.lokalflash.ch/api/health", 0),  # down (sustained) → critical
            ("https://www.lokalflash.ch/", 1),            # up → no problem
        ],
        cert_samples=[
            ("https://app.lokalflash.ch/api/health", 2.0),  # <3d → critical
            ("https://www.lokalflash.ch/", 10.0),           # <14d → warning
            ("https://other/", 40.0),                       # healthy → no problem
        ],
    ))
    ps = problems_from_blackbox("http://x", cert_warn_days=14, cert_crit_days=3)
    downs = [p for p in ps if p.category == "endpoint"]
    certs = {p.target: p for p in ps if p.category == "cert"}
    assert len(downs) == 1 and downs[0].level == "critical"
    assert downs[0].target == "https://app.lokalflash.ch/api/health"
    assert certs["https://app.lokalflash.ch/api/health"].level == "critical"
    assert certs["https://www.lokalflash.ch/"].level == "warning"
    assert "https://other/" not in certs   # 40d ahead → no problem
    assert all(p.vps == "ext" for p in ps)


def test_blackbox_prometheus_unreachable_is_empty(monkeypatch):
    def boom(*a, **k):
        raise alerting.httpx.ConnectError("refused")

    monkeypatch.setattr(alerting.httpx, "get", boom)
    assert problems_from_blackbox("http://x", cert_warn_days=14, cert_crit_days=3) == []


# --- state ---------------------------------------------------------------


def test_state_roundtrip(tmp_path: Path):
    path = tmp_path / "alert-state.json"
    probs = [Problem("critical", "cert", "vps-02", "a.ch", "x")]
    save_state(path, probs, now=NOW)
    assert load_state(path) == {probs[0].key}


def test_load_missing_state_is_empty(tmp_path: Path):
    assert load_state(tmp_path / "nope.json") == set()


# --- config --------------------------------------------------------------


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("VSA_ALERT_SMTP_USER", "alarms@lokalflash.ch")
    monkeypatch.setenv("VSA_ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("VSA_ALERT_TO", "alexandre@netcool.ch, info@flowbiz.ai")
    cfg = AlertConfig.from_env()
    assert cfg.recipients == ["alexandre@netcool.ch", "info@flowbiz.ai"]
    assert cfg.mail_from == "alarms@lokalflash.ch"  # defaults to user
    assert cfg.validate() == []


def test_config_validation_reports_missing():
    errs = AlertConfig().validate()
    assert any("SMTP" in e for e in errs)
    assert any("VSA_ALERT_TO" in e for e in errs)


# --- rendering -----------------------------------------------------------


def test_render_subject_counts():
    cfg = AlertConfig(subject_prefix="[VSA]")
    current = [
        Problem("critical", "cert", "v", "a", "x"),
        Problem("warning", "container", "v", "b", "y"),
    ]
    subj = alerting.render_subject(cfg, current, resolved=False)
    assert "1 critique" in subj and "1 warning" in subj
    assert "revenu à la normale" in alerting.render_subject(cfg, [], resolved=True)


def test_render_bodies_marks_new():
    cfg = AlertConfig()
    p = Problem("critical", "cert", "v", "a.ch", "expired")
    text, html = alerting.render_bodies(cfg, [p], [p], set(), now=NOW)
    assert "NOUVEAU" in text and "NOUVEAU" in html
    assert "a.ch" in html
