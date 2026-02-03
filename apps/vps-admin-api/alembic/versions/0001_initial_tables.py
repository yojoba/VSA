"""Initial tables.

Revision ID: 0001
Revises:
Create Date: 2026-02-03

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "vps_nodes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vps_id", sa.String(64), unique=True, nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False, server_default=""),
        sa.Column("ip_address", sa.String(45), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "domains",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), unique=True, nullable=False),
        sa.Column("vps_id", sa.String(64), nullable=False, server_default="vps-01"),
        sa.Column("container", sa.String(255), nullable=False, server_default=""),
        sa.Column("port", sa.Integer, nullable=False, server_default="3000"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "certificates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), unique=True, nullable=False),
        sa.Column(
            "issuer", sa.String(255), nullable=False, server_default="Let's Encrypt"
        ),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="valid"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("vps_id", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "actor", sa.String(128), nullable=False, server_default="", index=True
        ),
        sa.Column(
            "action", sa.String(128), nullable=False, server_default="", index=True
        ),
        sa.Column("target", sa.String(255), nullable=False, server_default=""),
        sa.Column("params", sa.Text, nullable=False, server_default="{}"),
        sa.Column("result", sa.String(32), nullable=False, server_default="success"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
    )

    op.create_table(
        "container_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vps_id", sa.String(64), nullable=False),
        sa.Column("container_name", sa.String(255), nullable=False),
        sa.Column("image", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(64), nullable=False, server_default=""),
        sa.Column("ports", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("container_snapshots")
    op.drop_table("audit_logs")
    op.drop_table("certificates")
    op.drop_table("domains")
    op.drop_table("vps_nodes")
