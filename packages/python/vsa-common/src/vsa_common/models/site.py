"""Site configuration model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    """Represents a provisioned site behind the reverse proxy."""

    domain: str
    container: str = ""
    port: int = 3000
    include_www: bool = True
    ssl: bool = True
    client_max_body_size: str = "20m"
    proxy_read_timeout: str = "120s"
    proxy_send_timeout: str = "120s"
    extra_location_directives: list[str] = Field(default_factory=list)
    auth_basic: bool = False
    auth_user: str | None = None
