"""Docker Compose stack status endpoints."""

from __future__ import annotations

import docker
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["stacks"])


def _get_docker_client():
    try:
        return docker.from_env()
    except docker.errors.DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}") from exc


@router.get("/stacks")
async def list_stacks():
    """List compose stacks by inspecting container labels."""
    client = _get_docker_client()
    containers = client.containers.list(all=True)

    stacks: dict[str, list[dict]] = {}
    for c in containers:
        project = c.labels.get("com.docker.compose.project", "")
        if not project:
            continue
        if project not in stacks:
            stacks[project] = []
        stacks[project].append({
            "name": c.name,
            "service": c.labels.get("com.docker.compose.service", ""),
            "status": c.status,
            "image": str(c.image.tags[0]) if c.image.tags else "",
        })

    return [
        {"name": name, "containers": containers}
        for name, containers in sorted(stacks.items())
    ]
