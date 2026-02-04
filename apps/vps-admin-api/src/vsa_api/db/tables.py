"""SQLAlchemy table definitions."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    vps_id: Mapped[str] = mapped_column(String(64), nullable=False, default="vps-01")
    container: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False, default="Let's Encrypt")
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="valid")


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
