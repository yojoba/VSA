"""Docker network operations for flowbiz_ext."""

from __future__ import annotations

from vsa.config import get_config
from vsa.services import docker


def ensure_external_network() -> None:
    """Create the shared Docker network if it doesn't exist."""
    cfg = get_config()
    if not docker.network_exists(cfg.docker_network):
        docker.network_create(cfg.docker_network)


def connect_container(container: str) -> None:
    """Connect a container to the shared Docker network."""
    cfg = get_config()
    ensure_external_network()
    docker.network_connect(cfg.docker_network, container)
