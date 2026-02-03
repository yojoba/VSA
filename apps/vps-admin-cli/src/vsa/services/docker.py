"""Docker and Docker Compose subprocess wrappers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from vsa.errors import ContainerNotFoundError, DockerError


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise DockerError(
            f"Command failed: {' '.join(cmd)}\nstderr: {exc.stderr}"
        ) from exc


def compose_up(compose_file: Path) -> None:
    _run(["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"])


def compose_down(compose_file: Path) -> None:
    _run(["docker", "compose", "-f", str(compose_file), "down"])


def compose_logs(compose_file: Path, tail: int = 200, follow: bool = True) -> None:
    cmd = ["docker", "compose", "-f", str(compose_file), "logs", f"--tail={tail}"]
    if follow:
        cmd.append("-f")
    subprocess.run(cmd, check=False)


def compose_ps(compose_file: Path) -> str:
    result = _run(["docker", "compose", "-f", str(compose_file), "ps"])
    return result.stdout


def compose_exec(compose_file: Path, service: str, *cmd: str) -> subprocess.CompletedProcess[str]:
    return _run(
        ["docker", "compose", "-f", str(compose_file), "exec", "-T", service, *cmd]
    )


def network_exists(name: str) -> bool:
    result = _run(["docker", "network", "inspect", name], check=False)
    return result.returncode == 0


def network_create(name: str) -> None:
    _run(["docker", "network", "create", name])


def network_connect(network: str, container: str) -> None:
    # Check if already connected
    result = _run(
        ["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", container],
        check=False,
    )
    if result.returncode == 0 and network in result.stdout:
        return
    _run(["docker", "network", "connect", network, container])


def find_container_by_port(external_port: int) -> str:
    """Find a running container publishing the given host port."""
    result = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and f"0.0.0.0:{external_port}->" in parts[1]:
            return parts[0]
    raise ContainerNotFoundError(f"No container found publishing port {external_port}")


def container_running(name: str) -> bool:
    result = _run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"
