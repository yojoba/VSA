"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from vsa_common import VsaConfig


@pytest.fixture
def tmp_config(tmp_path: Path) -> VsaConfig:
    """Return a VsaConfig pointing at temp directories."""
    (tmp_path / "stacks" / "reverse-proxy" / "nginx" / "conf.d").mkdir(parents=True)
    (tmp_path / "stacks" / "reverse-proxy" / "nginx" / "snippets").mkdir(parents=True)
    (tmp_path / "stacks" / "reverse-proxy" / "nginx" / "auth").mkdir(parents=True)
    return VsaConfig(
        vsa_root=tmp_path,
        vps_id="test-vps",
        srv_base=tmp_path / "srv",
        log_dir=tmp_path / "log",
        audit_jsonl_path=tmp_path / "log" / "audit.jsonl",
        audit_db_path=tmp_path / "lib" / "audit.db",
    )
