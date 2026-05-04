"""Tests for the certbot service — list_certs and health_report."""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vsa.services.certbot import (
    _parse_openssl_expiry,
    health_report,
    list_certs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_self_signed_cert(out_dir: Path, days: int = 90) -> None:
    """Generate a throwaway self-signed cert with the given remaining days."""
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:1024", "-nodes",
            "-keyout", str(out_dir / "privkey.pem"),
            "-out", str(out_dir / "cert.pem"),
            "-days", str(days),
            "-subj", f"/CN={out_dir.name}",
        ],
        capture_output=True, check=True,
    )
    # Mimic the LE layout: chain + fullchain
    shutil.copy(out_dir / "cert.pem", out_dir / "chain.pem")
    shutil.copy(out_dir / "cert.pem", out_dir / "fullchain.pem")


def _make_le_layout(
    le_dir: Path,
    domains: list[str],
    *,
    days: int = 90,
    create_symlinks: bool = True,
    create_renewal: bool = True,
    create_account: bool = True,
) -> None:
    """Build a fake /etc/letsencrypt/ directory structure.

    Mirrors what certbot creates: archive/<dom>/cert1.pem (real file) and
    live/<dom>/cert.pem (symlink into archive). Optionally skip pieces to
    simulate broken setups.
    """
    archive_root = le_dir / "archive"
    live_root = le_dir / "live"
    renewal_root = le_dir / "renewal"
    accounts_root = le_dir / "accounts" / "acme-v02.api.letsencrypt.org" / "directory"

    archive_root.mkdir(parents=True, exist_ok=True)
    live_root.mkdir(parents=True, exist_ok=True)
    renewal_root.mkdir(parents=True, exist_ok=True)

    if create_account:
        accounts_root.mkdir(parents=True, exist_ok=True)
        (accounts_root / "fake-account-id").mkdir(exist_ok=True)
        (accounts_root / "fake-account-id" / "regr.json").write_text("{}")

    for domain in domains:
        archive_dir = archive_root / domain
        live_dir = live_root / domain
        archive_dir.mkdir(parents=True, exist_ok=True)
        live_dir.mkdir(parents=True, exist_ok=True)

        # Generate a real cert into archive/<domain>/<n>1.pem
        _make_self_signed_cert(archive_dir, days=days)
        for fname in ("cert", "chain", "fullchain", "privkey"):
            archive_file = archive_dir / f"{fname}.pem"
            archive_file.rename(archive_dir / f"{fname}1.pem")

        # Wire up live/<domain>/<n>.pem
        for fname in ("cert", "chain", "fullchain", "privkey"):
            live_file = live_dir / f"{fname}.pem"
            archive_target = archive_dir / f"{fname}1.pem"
            if create_symlinks:
                live_file.symlink_to(Path("..") / ".." / "archive" / domain / f"{fname}1.pem")
            else:
                # The vps-02 footgun: regular file copy instead of symlink
                shutil.copy(archive_target, live_file)

        if create_renewal:
            renewal_root.joinpath(f"{domain}.conf").write_text(
                f"# fake renewal config for {domain}\nversion = 2.10.0\n"
                f"archive_dir = /etc/letsencrypt/archive/{domain}\n"
                "[renewalparams]\nauthenticator = webroot\n"
            )


# ---------------------------------------------------------------------------
# _parse_openssl_expiry
# ---------------------------------------------------------------------------


class TestParseOpensslExpiry:
    def test_double_space_day(self):
        # openssl emits a space-padded day for single-digit days
        dt = _parse_openssl_expiry("Aug  2 12:34:56 2026 GMT")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 8 and dt.day == 2

    def test_double_digit_day(self):
        dt = _parse_openssl_expiry("Mar 15 12:00:00 2099 GMT")
        assert dt is not None
        assert dt.year == 2099 and dt.month == 3 and dt.day == 15

    def test_garbage_returns_none(self):
        assert _parse_openssl_expiry("not a date") is None
        assert _parse_openssl_expiry("") is None


# ---------------------------------------------------------------------------
# list_certs
# ---------------------------------------------------------------------------


class TestListCerts:
    def test_empty_dir(self, tmp_path: Path):
        out = list_certs(tmp_path / "live")
        assert out == []

    def test_skips_readme(self, tmp_path: Path):
        _make_le_layout(tmp_path, [])
        readme = tmp_path / "live" / "README"
        readme.mkdir()
        (readme / "cert.pem").write_text("not a real cert")
        out = list_certs(tmp_path / "live")
        assert out == []

    def test_returns_certs(self, tmp_path: Path):
        _make_le_layout(tmp_path, ["a.example.test", "b.example.test"], days=120)
        out = list_certs(tmp_path / "live")
        domains = {row[0] for row in out}
        assert domains == {"a.example.test", "b.example.test"}
        # Each row's expiry should parse
        for _, expiry in out:
            assert _parse_openssl_expiry(expiry) is not None


# ---------------------------------------------------------------------------
# health_report
# ---------------------------------------------------------------------------


class TestHealthReport:
    def test_missing_letsencrypt_dir(self, tmp_path: Path):
        findings = health_report(tmp_path / "does-not-exist")
        assert any(f["kind"] == "no-letsencrypt-dir" for f in findings)

    def test_no_account_flagged(self, tmp_path: Path):
        _make_le_layout(tmp_path, ["x.example.test"], create_account=False)
        findings = health_report(tmp_path)
        kinds = [f["kind"] for f in findings]
        assert "no-le-account" in kinds

    def test_broken_symlink_flagged(self, tmp_path: Path):
        # The vps-02 footgun: live/<dom>/*.pem is a regular file, not a symlink
        _make_le_layout(tmp_path, ["x.example.test"], create_symlinks=False)
        findings = health_report(tmp_path)
        non_symlink = [f for f in findings if f["kind"] == "non-symlink"]
        # Four files: cert, chain, fullchain, privkey — all flagged
        assert len(non_symlink) == 4
        assert all(f["domain"] == "x.example.test" for f in non_symlink)

    def test_orphan_renewal_flagged(self, tmp_path: Path):
        _make_le_layout(tmp_path, ["x.example.test"], create_renewal=False)
        findings = health_report(tmp_path)
        kinds = [f["kind"] for f in findings]
        assert "orphan" in kinds

    def test_expiring_soon_flagged(self, tmp_path: Path):
        _make_le_layout(tmp_path, ["x.example.test"], days=20)  # within 30d
        findings = health_report(tmp_path)
        warns = [f for f in findings if f["kind"] == "expiring-soon"]
        assert len(warns) == 1
        assert warns[0]["level"] == "warning"

    def test_expiring_critical_flagged(self, tmp_path: Path):
        _make_le_layout(tmp_path, ["x.example.test"], days=10)  # within 14d
        findings = health_report(tmp_path)
        crits = [f for f in findings if f["kind"] == "expiring"]
        assert len(crits) == 1
        assert crits[0]["level"] == "critical"

    def test_healthy_layout_only_flags_unrelated(self, tmp_path: Path):
        # 90d cert, symlinks, renewal config, account — all good. Should be empty.
        _make_le_layout(tmp_path, ["x.example.test"], days=90)
        findings = health_report(tmp_path)
        # Filter out anything unrelated to our domain (defensive)
        relevant = [f for f in findings if f.get("domain") in (None, "x.example.test")]
        assert relevant == [], f"Unexpected findings on healthy layout: {relevant}"
