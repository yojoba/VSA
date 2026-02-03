"""Central configuration for VSA tools."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from vsa_common.constants import (
    AUDIT_DB_PATH,
    AUDIT_JSONL_PATH,
    CERTBOT_EMAIL,
    DOCKER_NETWORK,
    LOG_DIR,
    SRV_BASE,
)


def _default_vsa_root() -> Path:
    env = os.environ.get("VSA_ROOT")
    if env:
        return Path(env)
    # Walk up from this file to find the repo root (contains stacks/)
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "stacks").is_dir() and (parent / "Makefile").is_file():
            return parent
    # Fallback
    return Path.cwd()


class VsaConfig(BaseModel):
    """Runtime configuration resolved once at startup."""

    vsa_root: Path = Field(default_factory=_default_vsa_root)
    vps_id: str = Field(default_factory=lambda: os.environ.get("VSA_VPS_ID", "vps-01"))
    srv_base: Path = Field(default=SRV_BASE)
    docker_network: str = Field(default=DOCKER_NETWORK)
    certbot_email: str = Field(default_factory=lambda: os.environ.get("VSA_CERTBOT_EMAIL", CERTBOT_EMAIL))
    log_dir: Path = Field(default=LOG_DIR)
    audit_jsonl_path: Path = Field(default=AUDIT_JSONL_PATH)
    audit_db_path: Path = Field(default=AUDIT_DB_PATH)

    @property
    def stack_dir(self) -> Path:
        return self.vsa_root / "stacks"

    @property
    def reverse_proxy_dir(self) -> Path:
        return self.stack_dir / "reverse-proxy"

    @property
    def repo_vhost_dir(self) -> Path:
        return self.reverse_proxy_dir / "nginx" / "conf.d"

    @property
    def repo_snippets_dir(self) -> Path:
        return self.reverse_proxy_dir / "nginx" / "snippets"

    @property
    def repo_auth_dir(self) -> Path:
        return self.reverse_proxy_dir / "nginx" / "auth"

    @property
    def mount_vhost_dir(self) -> Path:
        return self.srv_base / "reverse-proxy" / "nginx" / "conf.d"

    @property
    def mount_snippets_dir(self) -> Path:
        return self.srv_base / "reverse-proxy" / "nginx" / "snippets"

    @property
    def mount_auth_dir(self) -> Path:
        return self.srv_base / "reverse-proxy" / "nginx" / "auth"

    @property
    def reverse_proxy_compose(self) -> Path:
        return self.reverse_proxy_dir / "compose.yml"
