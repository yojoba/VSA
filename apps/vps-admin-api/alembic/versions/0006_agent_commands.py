"""Add agent_commands queue — hub→agent execution channel.

Lets `vsa fleet exec --vps X -- <args>` (run on the hub) push a command into
this queue. The agent on each VPS polls for pending rows targeting its own
``vps_id`` every 30s, executes them via ``subprocess.run(["vsa", *args])``,
and writes the result (exit_code, stdout, stderr) back via
POST /agent/commands/{id}/result.

This is the foundation for Phase D fleet-aware mutations (`vsa fleet site
provision/failover/...`) — those will become thin orchestrators that enqueue
commands here.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_commands",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("vps_id", sa.String(64), nullable=False, index=True),
        # JSON array of argv elements after `vsa` — e.g. ["cert", "health"]
        sa.Column("argv", sa.JSON, nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column(
            "timeout_seconds",
            sa.Integer,
            nullable=False,
            server_default="120",
        ),
        sa.Column("requested_by", sa.String(128), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            index=True,
        ),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("stdout", sa.Text, nullable=True),
        sa.Column("stderr", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_commands")
