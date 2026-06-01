"""Tests for COMPOSE_FILE-aware docker compose argument building."""

from __future__ import annotations

from pathlib import Path

from vsa.services.docker import _compose_args, _read_compose_file_env


def _write(p: Path, text: str) -> Path:
    p.write_text(text)
    return p


def test_no_env_falls_back_to_single_file(tmp_path: Path) -> None:
    compose = _write(tmp_path / "compose.yml", "services: {}\n")
    assert _compose_args(compose) == ["-f", str(compose)]


def test_env_without_compose_file_falls_back(tmp_path: Path) -> None:
    compose = _write(tmp_path / "compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "FOO=bar\n# COMPOSE_FILE=ignored\n")
    assert _compose_args(compose) == ["-f", str(compose)]


def test_compose_file_override_expands_to_multiple_f_flags(tmp_path: Path) -> None:
    compose = _write(tmp_path / "compose.yml", "services: {}\n")
    _write(tmp_path / ".env", "COMPOSE_FILE=compose.yml:compose.dns-cloudflare.yml\n")
    assert _compose_args(compose) == [
        "-f", str(tmp_path / "compose.yml"),
        "-f", str(tmp_path / "compose.dns-cloudflare.yml"),
    ]


def test_compose_file_entries_resolved_relative_to_stack_dir(tmp_path: Path) -> None:
    compose = _write(tmp_path / "compose.yml", "services: {}\n")
    _write(tmp_path / ".env", 'COMPOSE_FILE="compose.yml:override.yml"\n')
    args = _compose_args(compose)
    assert args == ["-f", str(tmp_path / "compose.yml"), "-f", str(tmp_path / "override.yml")]


def test_semicolon_separator_supported(tmp_path: Path) -> None:
    env = _write(tmp_path / ".env", "COMPOSE_FILE=a.yml;b.yml\n")
    assert _read_compose_file_env(env) == ["a.yml", "b.yml"]


def test_empty_compose_file_treated_as_unset(tmp_path: Path) -> None:
    env = _write(tmp_path / ".env", "COMPOSE_FILE=\n")
    assert _read_compose_file_env(env) is None


def test_absolute_paths_preserved(tmp_path: Path) -> None:
    compose = _write(tmp_path / "compose.yml", "services: {}\n")
    abs_override = "/etc/compose.dns.yml"
    _write(tmp_path / ".env", f"COMPOSE_FILE=compose.yml:{abs_override}\n")
    assert _compose_args(compose) == [
        "-f", str(tmp_path / "compose.yml"),
        "-f", abs_override,
    ]
