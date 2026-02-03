"""HTTP Basic Auth — bcrypt-based htpasswd generation."""

from __future__ import annotations

from pathlib import Path

import bcrypt


def hash_password(password: str) -> str:
    """Generate a bcrypt hash suitable for NGINX htpasswd files."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode(), salt)
    return hashed.decode()


def create_htpasswd(username: str, password: str) -> str:
    """Return a single htpasswd line: user:{bcrypt}hash."""
    return f"{username}:{hash_password(password)}"


def write_htpasswd_file(path: Path, username: str, password: str) -> None:
    """Write an htpasswd file with a single user entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(create_htpasswd(username, password) + "\n")


def read_htpasswd_users(path: Path) -> list[str]:
    """Return list of usernames from an htpasswd file."""
    if not path.exists():
        return []
    users = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and ":" in line:
            users.append(line.split(":")[0])
    return users
