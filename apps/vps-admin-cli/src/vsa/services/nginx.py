"""NGINX config validation and reload."""

from __future__ import annotations

from pathlib import Path

from vsa.errors import NginxConfigError
from vsa.services import docker


def validate_config(compose_file: Path) -> None:
    """Run nginx -t inside the container. Raises NginxConfigError on failure."""
    result = docker._run(
        ["docker", "compose", "-f", str(compose_file), "exec", "-T", "nginx", "nginx", "-t"],
        check=False,
    )
    if result.returncode != 0:
        raise NginxConfigError(f"NGINX config test failed:\n{result.stderr}")


def reload(compose_file: Path) -> None:
    """Validate config, then reload NGINX."""
    validate_config(compose_file)
    docker.compose_exec(compose_file, "nginx", "nginx", "-s", "reload")


def reload_unsafe(compose_file: Path) -> None:
    """Reload NGINX without validation (for cases where validation isn't possible)."""
    docker.compose_exec(compose_file, "nginx", "nginx", "-s", "reload")
