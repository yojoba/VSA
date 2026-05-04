"""Add domain_assignments registry — primary VPS + standby VPS list per domain.

This is the first piece of write-side multi-VPS awareness in VSA. The agent-
synced ``domains`` and ``certificates`` tables are still per-VPS observations
(what the agent sees on its host). ``domain_assignments`` is a single row per
domain that records the user's *intent* — which VPS is the primary and which
are warm standbys.

Failover, drift detection, and fleet-wide health checks all read from this
table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "domain_assignments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("primary_vps_id", sa.String(64), nullable=False),
        sa.Column(
            "standby_vps_ids",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("domain_assignments")
