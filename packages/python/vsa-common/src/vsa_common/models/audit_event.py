"""Audit event model for ISO 27001-compliant logging."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """A single auditable operation."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    vps_id: str = "vps-01"
    actor: str = ""
    action: str = ""
    target: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: str = "success"
    error: str | None = None
    duration_ms: int | None = None

    def to_jsonl(self) -> str:
        return self.model_dump_json()
