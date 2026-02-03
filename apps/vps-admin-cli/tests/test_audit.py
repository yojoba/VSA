"""Tests for audit logging."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from vsa_common import AuditEvent, VsaConfig
from vsa.audit import _write_jsonl, _write_sqlite, _init_db, audit


class TestAuditJSONL:
    def test_write_jsonl(self, tmp_path: Path):
        path = tmp_path / "audit.jsonl"
        event = AuditEvent(
            action="test.write",
            target="example.com",
            actor="tester",
            vps_id="test-vps",
        )
        _write_jsonl(path, event)

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["action"] == "test.write"
        assert data["target"] == "example.com"

    def test_append_jsonl(self, tmp_path: Path):
        path = tmp_path / "audit.jsonl"
        for i in range(3):
            event = AuditEvent(action=f"test.{i}", target="target")
            _write_jsonl(path, event)

        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3


class TestAuditSQLite:
    def test_write_sqlite(self, tmp_path: Path):
        db_path = tmp_path / "audit.db"
        event = AuditEvent(
            action="test.sqlite",
            target="example.com",
            actor="tester",
            vps_id="test-vps",
        )
        _write_sqlite(db_path, event)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT action, target, actor FROM audit_logs").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0] == ("test.sqlite", "example.com", "tester")


class TestAuditContextManager:
    def test_success(self, tmp_config: VsaConfig):
        with patch("vsa.audit.get_config", return_value=tmp_config):
            with audit("test.success", target="test.com") as event:
                pass

        assert event.result == "success"
        assert event.duration_ms is not None
        assert event.duration_ms >= 0

        # Check JSONL was written
        lines = tmp_config.audit_jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["action"] == "test.success"

    def test_failure(self, tmp_config: VsaConfig):
        with patch("vsa.audit.get_config", return_value=tmp_config):
            try:
                with audit("test.failure", target="fail.com") as event:
                    raise ValueError("something broke")
            except ValueError:
                pass

        assert event.result == "failure"
        assert event.error == "something broke"

        lines = tmp_config.audit_jsonl_path.read_text().strip().splitlines()
        data = json.loads(lines[0])
        assert data["result"] == "failure"
