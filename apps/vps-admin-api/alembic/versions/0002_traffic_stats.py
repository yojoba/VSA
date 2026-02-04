"""Add traffic_stats table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-03

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "traffic_stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(255), nullable=False, index=True),
        sa.Column("vps_id", sa.String(64), nullable=False, server_default="vps-01"),
        sa.Column(
            "period_start",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requests", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status_2xx", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status_3xx", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status_4xx", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status_5xx", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_sent", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "avg_request_time_ms", sa.Integer, nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_table("traffic_stats")
