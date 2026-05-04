"""Add ``sans`` JSON column to certificates — Subject Alternative Names.

Lets the fleet drift endpoint know that one cert can cover multiple names
(e.g. ``lokalflash.ch`` cert with ``www.lokalflash.ch`` in its SAN), so
``www.lokalflash.ch`` shouldn't be flagged as cert-missing on a host that
already serves the apex.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "certificates",
        sa.Column(
            "sans",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )


def downgrade() -> None:
    op.drop_column("certificates", "sans")
