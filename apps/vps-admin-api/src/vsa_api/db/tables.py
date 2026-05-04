"""SQLAlchemy table definitions."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from vsa_api.db.session import Base


class VpsNode(Base):
    __tablename__ = "vps_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vps_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Domain(Base):
    __tablename__ = "domains"
    __table_args__ = (
        UniqueConstraint("vps_id", "domain", name="uq_domains_vps_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Note: composite unique with vps_id (see __table_args__) — a single
    # domain can be pre-positioned on multiple VPS for warm-standby.
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False, default="vps-01")
    container: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("vps_id", "domain", name="uq_certificates_vps_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Composite unique with vps_id — same cert may be pre-deployed on a
    # standby VPS while still active on the primary.
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    vps_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="vps-01", server_default="vps-01"
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False, default="Let's Encrypt")
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="valid")
    # Subject Alternative Names — populated by the agent from `openssl x509 -ext
    # subjectAltName`. Includes the primary CN; lets the fleet drift endpoint
    # know one cert can cover multiple names (apex + www, etc.).
    sans: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ContainerSnapshot(Base):
    __tablename__ = "container_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False)
    container_name: Mapped[str] = mapped_column(String(255), nullable=False)
    image: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ports: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compose_project: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    compose_service: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DomainAssignment(Base):
    """Intended placement of a domain across the fleet — primary + standbys.

    Distinct from the agent-synced ``domains`` and ``certificates`` tables
    (which record observed state per VPS). Edited by hand on the hub via the
    ``/api/assignments`` endpoint or `vsa fleet assign` CLI command.
    """

    __tablename__ = "domain_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    primary_vps_id: Mapped[str] = mapped_column(String(64), nullable=False)
    standby_vps_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentCommand(Base):
    """Hub→agent execution queue — `vsa fleet exec` enqueues here.

    Status lifecycle: ``pending`` → ``running`` (agent took it) →
    ``completed`` (any exit code) or ``timeout`` (agent never came back).
    """

    __tablename__ = "agent_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    argv: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120"
    )
    requested_by: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    taken_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrafficStat(Base):
    __tablename__ = "traffic_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False, default="vps-01")
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_2xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_3xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_4xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_5xx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    avg_request_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
