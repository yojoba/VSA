"""Tests for bcrypt htpasswd service."""

from __future__ import annotations

from pathlib import Path

import bcrypt

from vsa.services.htpasswd import (
    create_htpasswd,
    hash_password,
    read_htpasswd_users,
    write_htpasswd_file,
)


class TestHtpasswd:
    def test_hash_password_is_bcrypt(self):
        hashed = hash_password("secret123")
        # bcrypt hashes start with $2b$
        assert hashed.startswith("$2b$")
        # Verify the hash
        assert bcrypt.checkpw(b"secret123", hashed.encode())

    def test_create_htpasswd_line(self):
        line = create_htpasswd("admin", "password")
        assert line.startswith("admin:$2b$")
        user, hashed = line.split(":", 1)
        assert user == "admin"
        assert bcrypt.checkpw(b"password", hashed.encode())

    def test_write_and_read(self, tmp_path: Path):
        path = tmp_path / "test.htpasswd"
        write_htpasswd_file(path, "testuser", "testpass")
        assert path.exists()

        users = read_htpasswd_users(path)
        assert users == ["testuser"]

        # Verify password works
        content = path.read_text().strip()
        _, hashed = content.split(":", 1)
        assert bcrypt.checkpw(b"testpass", hashed.encode())

    def test_read_nonexistent(self, tmp_path: Path):
        users = read_htpasswd_users(tmp_path / "nonexistent.htpasswd")
        assert users == []

    def test_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "auth.htpasswd"
        write_htpasswd_file(path, "user", "pass")
        assert path.exists()
