"""Fleet-wide drift and health endpoints (Phase E).

Cross-checks the user-edited intent (``domain_assignments``) against the
agent-observed reality (``domains`` and ``certificates``). The endpoint
is read-only — fixing drift is a manual decision (``vsa fleet
site-provision`` / ``vsa fleet exec``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vsa_api.db.session import get_db
from vsa_api.db.tables import Certificate, Domain, DomainAssignment

router = APIRouter(tags=["fleet"])

WARNING_DAYS = 30
CRITICAL_DAYS = 14


def _finding(level: str, kind: str, domain: str | None, vps_id: str | None, message: str) -> dict[str, Any]:
    return {
        "level": level,
        "kind": kind,
        "domain": domain,
        "vps_id": vps_id,
        "message": message,
    }


@router.get("/fleet/drift")
async def fleet_drift(db: AsyncSession = Depends(get_db)):
    """Report drift between intended and observed fleet state.

    Findings:
    - ``missing-on-primary``: assignment exists but no Domain row on
      ``primary_vps_id`` (the active host has no vhost for it).
    - ``missing-on-standby``: assignment lists a standby that has no
      Domain row for this domain.
    - ``rogue-host``: a Domain row exists on a vps_id that's neither the
      primary nor a listed standby — surprise host with this vhost.
    - ``primary-cert-missing``: assignment exists but no Certificate row
      on the primary VPS.
    - ``standby-cert-missing``: assignment lists a standby with no cert
      for this domain (so failover would land on a 502 + no TLS).
    - ``cert-expiring-soon``: cert on primary or standby expires within
      30 days (warning) or 14 days (critical).
    - ``domain-without-assignment``: a Domain row exists in some VPS
      but no row in domain_assignments — operator hasn't claimed it.
    """
    # Fetch everything once, do the joins in Python — these tables are
    # small (low hundreds of rows max).
    assignments = (await db.execute(select(DomainAssignment))).scalars().all()
    domains = (await db.execute(select(Domain))).scalars().all()
    certs = (await db.execute(select(Certificate))).scalars().all()

    # Indexes for cross-lookup
    domain_by_pair: dict[tuple[str, str], Domain] = {(d.domain, d.vps_id): d for d in domains}
    cert_by_pair: dict[tuple[str, str], Certificate] = {(c.domain, c.vps_id): c for c in certs}
    domain_vps_ids: dict[str, set[str]] = {}
    for d in domains:
        domain_vps_ids.setdefault(d.domain, set()).add(d.vps_id)

    now = datetime.now(timezone.utc)
    warning_cutoff = now + timedelta(days=WARNING_DAYS)
    critical_cutoff = now + timedelta(days=CRITICAL_DAYS)

    findings: list[dict[str, Any]] = []
    assigned_domains: set[str] = set()

    for a in assignments:
        assigned_domains.add(a.domain)
        expected_vps_ids = {a.primary_vps_id, *(a.standby_vps_ids or [])}

        # missing-on-primary
        if (a.domain, a.primary_vps_id) not in domain_by_pair:
            findings.append(
                _finding(
                    "critical",
                    "missing-on-primary",
                    a.domain,
                    a.primary_vps_id,
                    f"primary {a.primary_vps_id} has no vhost for {a.domain}",
                )
            )

        # missing-on-standby
        for sb in a.standby_vps_ids or []:
            if (a.domain, sb) not in domain_by_pair:
                findings.append(
                    _finding(
                        "warning",
                        "missing-on-standby",
                        a.domain,
                        sb,
                        f"standby {sb} has no vhost for {a.domain} (failover will fail)",
                    )
                )

        # rogue-host
        for vps_id in domain_vps_ids.get(a.domain, set()) - expected_vps_ids:
            findings.append(
                _finding(
                    "warning",
                    "rogue-host",
                    a.domain,
                    vps_id,
                    f"{vps_id} has a vhost for {a.domain} but isn't primary or standby in the registry",
                )
            )

        # cert checks per expected VPS
        for vps_id in expected_vps_ids:
            cert = cert_by_pair.get((a.domain, vps_id))
            if cert is None:
                level = "critical" if vps_id == a.primary_vps_id else "warning"
                kind = "primary-cert-missing" if vps_id == a.primary_vps_id else "standby-cert-missing"
                findings.append(
                    _finding(
                        level,
                        kind,
                        a.domain,
                        vps_id,
                        f"no cert observed for {a.domain} on {vps_id}",
                    )
                )
                continue
            if cert.expiry is not None:
                expiry = cert.expiry if cert.expiry.tzinfo else cert.expiry.replace(tzinfo=timezone.utc)
                if expiry < now:
                    findings.append(
                        _finding(
                            "critical",
                            "cert-expired",
                            a.domain,
                            vps_id,
                            f"cert expired on {expiry.date().isoformat()}",
                        )
                    )
                elif expiry < critical_cutoff:
                    days = (expiry - now).days
                    findings.append(
                        _finding(
                            "critical",
                            "cert-expiring-soon",
                            a.domain,
                            vps_id,
                            f"cert expires in {days}d",
                        )
                    )
                elif expiry < warning_cutoff:
                    days = (expiry - now).days
                    findings.append(
                        _finding(
                            "warning",
                            "cert-expiring-soon",
                            a.domain,
                            vps_id,
                            f"cert expires in {days}d",
                        )
                    )

    # domain-without-assignment — observed domains not in the registry
    for domain in sorted(set(domain_vps_ids) - assigned_domains):
        for vps_id in sorted(domain_vps_ids[domain]):
            findings.append(
                _finding(
                    "info",
                    "domain-without-assignment",
                    domain,
                    vps_id,
                    f"observed on {vps_id} but no entry in domain_assignments",
                )
            )

    summary = {
        "critical": sum(1 for f in findings if f["level"] == "critical"),
        "warning": sum(1 for f in findings if f["level"] == "warning"),
        "info": sum(1 for f in findings if f["level"] == "info"),
        "total": len(findings),
    }
    return {"summary": summary, "findings": findings}
