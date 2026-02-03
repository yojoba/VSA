"""API configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://vsa:vsa@localhost:5432/vsa"
    docker_socket: str = "unix:///var/run/docker.sock"
    audit_jsonl_path: str = "/var/log/vsa/audit.jsonl"
    audit_db_path: str = "/var/lib/vsa/audit.db"
    cors_origins: list[str] = ["http://localhost:3000"]
    api_token: str = ""  # Pre-shared token for agent auth

    model_config = {"env_prefix": "VSA_"}


settings = Settings()
