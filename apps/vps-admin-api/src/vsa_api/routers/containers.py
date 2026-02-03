"""Container management endpoints — live Docker queries."""

from __future__ import annotations

import docker
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["containers"])


def _get_docker_client():
    try:
        return docker.from_env()
    except docker.errors.DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}") from exc


@router.get("/containers")
async def list_containers():
    """List all running Docker containers."""
    client = _get_docker_client()
    containers = client.containers.list(all=True)
    return [
        {
            "name": c.name,
            "image": str(c.image.tags[0]) if c.image.tags else str(c.image.id[:12]),
            "status": c.status,
            "ports": c.ports,
            "labels": {
                k: v for k, v in c.labels.items()
                if k.startswith("com.docker.compose.")
            },
        }
        for c in containers
    ]


@router.get("/containers/{name}")
async def get_container(name: str):
    """Get details for a specific container."""
    client = _get_docker_client()
    try:
        c = client.containers.get(name)
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{name}' not found")
    return {
        "name": c.name,
        "image": str(c.image.tags[0]) if c.image.tags else str(c.image.id[:12]),
        "status": c.status,
        "ports": c.ports,
        "networks": list(c.attrs.get("NetworkSettings", {}).get("Networks", {}).keys()),
        "created": c.attrs.get("Created"),
    }
