"""Add compose_project column to container_snapshots.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "container_snapshots",
        sa.Column(
            "compose_project",
            sa.String(255),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("container_snapshots", "compose_project")
