"""Dual-write audit logger: JSONL file + SQLite database."""

from __future__ import annotations

import getpass
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from vsa_common import AuditEvent

from vsa.config import get_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    vps_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT 'success',
    error TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor);
"""


def _ensure_dirs(cfg: Any) -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    cfg.audit_db_path.parent.mkdir(parents=True, exist_ok=True)


def _get_actor() -> str:
    return os.environ.get("VSA_ACTOR") or getpass.getuser()


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    return conn


def _write_jsonl(path: Path, event: AuditEvent) -> None:
    with open(path, "a") as f:
        f.write(event.to_jsonl() + "\n")


def _write_sqlite(db_path: Path, event: AuditEvent) -> None:
    conn = _init_db(db_path)
    try:
        conn.execute(
            """INSERT INTO audit_logs
               (timestamp, vps_id, actor, action, target, params, result, error, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.timestamp.isoformat(),
                event.vps_id,
                event.actor,
                event.action,
                event.target,
                event.model_dump_json(include={"params"}),
                event.result,
                event.error,
                event.duration_ms,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def log_event(event: AuditEvent) -> None:
    """Write an audit event to both JSONL and SQLite."""
    cfg = get_config()
    _ensure_dirs(cfg)
    _write_jsonl(cfg.audit_jsonl_path, event)
    _write_sqlite(cfg.audit_db_path, event)


@contextmanager
def audit(action: str, target: str = "", **params: Any) -> Generator[AuditEvent, None, None]:
    """Context manager that records timing and success/failure."""
    cfg = get_config()
    event = AuditEvent(
        vps_id=cfg.vps_id,
        actor=_get_actor(),
        action=action,
        target=target,
        params=params,
    )
    start = time.monotonic()
    try:
        yield event
        event.result = "success"
    except Exception as exc:
        event.result = "failure"
        event.error = str(exc)
        raise
    finally:
        event.duration_ms = int((time.monotonic() - start) * 1000)
        log_event(event)
