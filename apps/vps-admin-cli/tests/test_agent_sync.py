"""Tests for agent_sync service — collectors and sync state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from vsa.services.agent_sync import (
    _load_sync_state,
    _save_sync_state,
    collect_domains,
    collect_traffic_stats,
    collect_unsent_audit_events,
    parse_cert_output,
)


# ---------------------------------------------------------------------------
# Domain collection
# ---------------------------------------------------------------------------


class TestCollectDomains:
    def test_single_upstream(self, tmp_path: Path):
        conf = tmp_path / "example.com.conf"
        conf.write_text(
            "server {\n"
            "  server_name example.com;\n"
            "  resolver 127.0.0.11 ipv6=off;\n"
            "  set $upstream myapp:3000;\n"
            "  location / { proxy_pass http://$upstream; }\n"
            "}\n"
        )
        result = collect_domains(tmp_path)
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"
        assert result[0]["container"] == "myapp"
        assert result[0]["port"] == 3000

    def test_multiple_upstreams_takes_first(self, tmp_path: Path):
        """For configs with $api_upstream and $ui_upstream, take the first match."""
        conf = tmp_path / "dashboard.flowbiz.ai.conf"
        conf.write_text(
            "server {\n"
            "  set $api_upstream dashboard-api:8000;\n"
            "  set $ui_upstream dashboard-ui:3000;\n"
            "}\n"
        )
        result = collect_domains(tmp_path)
        assert len(result) == 1
        assert result[0]["domain"] == "dashboard.flowbiz.ai"
        assert result[0]["container"] == "dashboard-api"
        assert result[0]["port"] == 8000

    def test_no_upstream_skipped(self, tmp_path: Path):
        conf = tmp_path / "static.com.conf"
        conf.write_text("server { server_name static.com; }\n")
        result = collect_domains(tmp_path)
        assert len(result) == 0

    def test_empty_dir(self, tmp_path: Path):
        result = collect_domains(tmp_path)
        assert result == []

    def test_nonexistent_dir(self, tmp_path: Path):
        result = collect_domains(tmp_path / "does_not_exist")
        assert result == []


# ---------------------------------------------------------------------------
# Certificate output parsing
# ---------------------------------------------------------------------------


class TestParseCertOutput:
    def test_valid_output(self):
        raw = "example.com|Mar 15 12:00:00 2099 GMT\nother.com|Jan  1 00:00:00 2099 GMT\n"
        result = parse_cert_output(raw)
        assert len(result) == 2
        assert result[0]["domain"] == "example.com"
        assert result[0]["status"] == "valid"
        assert result[0]["issuer"] == "Let's Encrypt"
        assert "2099" in result[0]["expiry"]

    def test_expired_cert(self):
        raw = "old.com|Jan  1 00:00:00 2020 GMT\n"
        result = parse_cert_output(raw)
        assert len(result) == 1
        assert result[0]["status"] == "expired"

    def test_empty_output(self):
        assert parse_cert_output("") == []
        assert parse_cert_output("\n") == []

    def test_malformed_line(self):
        raw = "no-pipe-here\n"
        result = parse_cert_output(raw)
        assert result == []


# ---------------------------------------------------------------------------
# Sync state persistence
# ---------------------------------------------------------------------------


class TestSyncState:
    def test_roundtrip(self, tmp_path: Path):
        state_path = tmp_path / "state.json"
        state = {"last_audit_id": 42}
        with patch("vsa.services.agent_sync._STATE_PATH", state_path):
            _save_sync_state(state)
            loaded = _load_sync_state()
        assert loaded["last_audit_id"] == 42

    def test_missing_file(self, tmp_path: Path):
        state_path = tmp_path / "nonexistent.json"
        with patch("vsa.services.agent_sync._STATE_PATH", state_path):
            loaded = _load_sync_state()
        assert loaded == {"last_audit_id": 0, "file_offsets": {}}


# ---------------------------------------------------------------------------
# Unsent audit events
# ---------------------------------------------------------------------------


class TestCollectUnsentAuditEvents:
    def _create_db(self, db_path: Path, n: int = 5) -> None:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE audit_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT, vps_id TEXT, actor TEXT, action TEXT, "
            "target TEXT, params TEXT, result TEXT, error TEXT, duration_ms INTEGER)"
        )
        for i in range(1, n + 1):
            conn.execute(
                "INSERT INTO audit_logs (timestamp, vps_id, actor, action, target, params, result) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"2024-01-0{i}", "vps-01", "tester", f"action.{i}", "target", "{}", "success"),
            )
        conn.commit()
        conn.close()

    def test_reads_after_last_id(self, tmp_path: Path):
        db_path = tmp_path / "audit.db"
        self._create_db(db_path, n=5)
        events, max_id = collect_unsent_audit_events(db_path, last_id=2)
        assert len(events) == 3
        assert max_id == 5
        assert events[0]["action"] == "action.3"

    def test_no_new_events(self, tmp_path: Path):
        db_path = tmp_path / "audit.db"
        self._create_db(db_path, n=3)
        events, max_id = collect_unsent_audit_events(db_path, last_id=3)
        assert events == []
        assert max_id == 3

    def test_missing_db(self, tmp_path: Path):
        events, max_id = collect_unsent_audit_events(tmp_path / "nope.db", last_id=0)
        assert events == []
        assert max_id == 0

    def test_from_zero(self, tmp_path: Path):
        db_path = tmp_path / "audit.db"
        self._create_db(db_path, n=3)
        events, max_id = collect_unsent_audit_events(db_path, last_id=0)
        assert len(events) == 3
        assert max_id == 3


# ---------------------------------------------------------------------------
# Traffic stats collection
# ---------------------------------------------------------------------------


class TestCollectTrafficStats:
    def _write_log(self, log_dir: Path, domain: str, entries: list[dict]) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{domain}.access.json"
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_basic_aggregation(self, tmp_path: Path):
        log_dir = tmp_path / "domains"
        entries = [
            {
                "time": "2026-02-03T10:00:00+01:00",
                "domain": "example.com",
                "remote_addr": "1.2.3.4",
                "method": "GET",
                "uri": "/",
                "status": 200,
                "body_bytes_sent": 1024,
                "request_time": 0.05,
                "http_user_agent": "Mozilla/5.0",
            },
            {
                "time": "2026-02-03T10:00:01+01:00",
                "domain": "example.com",
                "remote_addr": "1.2.3.5",
                "method": "POST",
                "uri": "/api",
                "status": 404,
                "body_bytes_sent": 256,
                "request_time": 0.15,
                "http_user_agent": "curl/8.0",
            },
            {
                "time": "2026-02-03T10:00:02+01:00",
                "domain": "example.com",
                "remote_addr": "1.2.3.6",
                "method": "GET",
                "uri": "/error",
                "status": 500,
                "body_bytes_sent": 512,
                "request_time": 1.0,
                "http_user_agent": "bot",
            },
        ]
        self._write_log(log_dir, "example.com", entries)

        stats, offsets = collect_traffic_stats(log_dir, {})
        assert len(stats) == 1
        s = stats[0]
        assert s["domain"] == "example.com"
        assert s["requests"] == 3
        assert s["status_2xx"] == 1
        assert s["status_4xx"] == 1
        assert s["status_5xx"] == 1
        assert s["status_3xx"] == 0
        assert s["bytes_sent"] == 1024 + 256 + 512
        # avg = (0.05 + 0.15 + 1.0) / 3 * 1000 ~ 400ms (allow float rounding)
        assert 399 <= s["avg_request_time_ms"] <= 400
        assert s["period_start"] == "2026-02-03T10:00:00+01:00"
        assert s["period_end"] == "2026-02-03T10:00:02+01:00"
        # Offset should be set
        assert "example.com.access.json" in offsets
        assert offsets["example.com.access.json"] > 0

    def test_incremental_offset(self, tmp_path: Path):
        """Second call with saved offset should return no new stats."""
        log_dir = tmp_path / "domains"
        entries = [
            {
                "time": "2026-02-03T10:00:00+01:00",
                "domain": "test.com",
                "status": 200,
                "body_bytes_sent": 100,
                "request_time": 0.01,
            },
        ]
        self._write_log(log_dir, "test.com", entries)

        stats1, offsets1 = collect_traffic_stats(log_dir, {})
        assert len(stats1) == 1

        # Second call with saved offsets — no new data
        stats2, offsets2 = collect_traffic_stats(log_dir, offsets1)
        assert len(stats2) == 0
        assert offsets2 == offsets1

    def test_empty_dir(self, tmp_path: Path):
        log_dir = tmp_path / "domains"
        log_dir.mkdir()
        stats, offsets = collect_traffic_stats(log_dir, {})
        assert stats == []
        assert offsets == {}

    def test_nonexistent_dir(self, tmp_path: Path):
        stats, offsets = collect_traffic_stats(tmp_path / "nope", {})
        assert stats == []
        assert offsets == {}

    def test_multiple_domains(self, tmp_path: Path):
        log_dir = tmp_path / "domains"
        self._write_log(
            log_dir,
            "a.com",
            [{"time": "t1", "status": 200, "body_bytes_sent": 10, "request_time": 0.1}],
        )
        self._write_log(
            log_dir,
            "b.com",
            [{"time": "t2", "status": 301, "body_bytes_sent": 20, "request_time": 0.2}],
        )
        stats, _ = collect_traffic_stats(log_dir, {})
        assert len(stats) == 2
        domains = {s["domain"] for s in stats}
        assert domains == {"a.com", "b.com"}

    def test_string_status_and_bytes(self, tmp_path: Path):
        """Status and bytes as strings should be parsed correctly."""
        log_dir = tmp_path / "domains"
        self._write_log(
            log_dir,
            "str.com",
            [{"time": "t1", "status": "200", "body_bytes_sent": "500", "request_time": "0.05"}],
        )
        stats, _ = collect_traffic_stats(log_dir, {})
        assert stats[0]["status_2xx"] == 1
        assert stats[0]["bytes_sent"] == 500


# ---------------------------------------------------------------------------
# Container collection (mocked subprocess)
# ---------------------------------------------------------------------------


class TestCollectContainers:
    def test_parses_docker_json(self):
        from vsa.services.agent_sync import collect_containers

        docker_output = (
            '{"Names":"nginx","Image":"nginx:1.25","Status":"Up 2 hours","Ports":"0.0.0.0:80->80/tcp"}\n'
            '{"Names":"api","Image":"myapi:latest","Status":"Up 1 hour","Ports":"8000/tcp"}\n'
        )
        fake_result = type("R", (), {"returncode": 0, "stdout": docker_output, "stderr": ""})()
        with patch("vsa.services.agent_sync.subprocess.run", return_value=fake_result):
            result = collect_containers()
        assert len(result) == 2
        assert result[0]["name"] == "nginx"
        assert result[0]["image"] == "nginx:1.25"
        assert result[1]["name"] == "api"

    def test_docker_failure_returns_empty(self):
        from vsa.services.agent_sync import collect_containers

        fake_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        with patch("vsa.services.agent_sync.subprocess.run", return_value=fake_result):
            result = collect_containers()
        assert result == []
