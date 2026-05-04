"""Make domains, certificates, and stacks fully multi-VPS aware.

Changes:
* Add ``compose_service`` to ``container_snapshots`` (lets us reconstruct
  stacks across VPS without falling back to the local Docker socket).
* Add ``vps_id`` to ``certificates`` so the same cert can coexist on
  primary + warm-standby VPS.
* Switch ``domains.domain`` and ``certificates.domain`` from ``UNIQUE``
  to composite ``UNIQUE (vps_id, domain)``. Keep the bare ``domain``
  column indexed for lookups.

Postgres-only (the project uses PG everywhere — see alembic.ini).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-04

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- container_snapshots: compose_service -----------------------------
    op.add_column(
        "container_snapshots",
        sa.Column(
            "compose_service",
            sa.String(255),
            nullable=False,
            server_default="",
        ),
    )

    # --- certificates: add vps_id, swap unique constraint ----------------
    op.add_column(
        "certificates",
        sa.Column(
            "vps_id",
            sa.String(64),
            nullable=False,
            server_default="vps-01",
        ),
    )
    # Auto-named unique on certificates.domain from initial migration.
    op.drop_constraint("certificates_domain_key", "certificates", type_="unique")
    op.create_index("ix_certificates_domain", "certificates", ["domain"])
    op.create_unique_constraint(
        "uq_certificates_vps_domain", "certificates", ["vps_id", "domain"]
    )

    # --- domains: swap unique constraint ---------------------------------
    op.drop_constraint("domains_domain_key", "domains", type_="unique")
    op.create_index("ix_domains_domain", "domains", ["domain"])
    op.create_unique_constraint(
        "uq_domains_vps_domain", "domains", ["vps_id", "domain"]
    )


def downgrade() -> None:
    # --- domains ----------------------------------------------------------
    op.drop_constraint("uq_domains_vps_domain", "domains", type_="unique")
    op.drop_index("ix_domains_domain", table_name="domains")
    op.create_unique_constraint("domains_domain_key", "domains", ["domain"])

    # --- certificates -----------------------------------------------------
    op.drop_constraint("uq_certificates_vps_domain", "certificates", type_="unique")
    op.drop_index("ix_certificates_domain", table_name="certificates")
    op.create_unique_constraint("certificates_domain_key", "certificates", ["domain"])
    op.drop_column("certificates", "vps_id")

    # --- container_snapshots ---------------------------------------------
    op.drop_column("container_snapshots", "compose_service")
