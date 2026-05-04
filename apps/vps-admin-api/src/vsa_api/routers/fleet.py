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

    # SAN-aware lookups: a vhost or cert that names www.X also serves X (and
    # vice-versa). For each (vps_id), collect the union of all primary CNs +
    # SANs across the certs observed there. Drift queries consult these sets
    # before flagging a domain as missing.
    sans_by_vps: dict[str, set[str]] = {}
    cert_owner_by_pair: dict[tuple[str, str], Certificate] = dict(cert_by_pair)
    for c in certs:
        names = {c.domain, *(c.sans or [])}
        sans_by_vps.setdefault(c.vps_id, set()).update(names)
        for name in names:
            cert_owner_by_pair.setdefault((name, c.vps_id), c)

    # Vhosts can also serve multiple names (server_name a.com www.a.com)
    # but the agent only reports the file's stem today. As a heuristic,
    # treat domains observed without their `www.` sibling — and vice
    # versa — as covered when one of the pair exists. Stronger fix would
    # be to have the agent emit each `server_name` token; v2.
    domain_aliases_by_vps: dict[str, set[str]] = {}
    for vps_id, names in [(d.vps_id, d.domain) for d in domains]:
        domain_aliases_by_vps.setdefault(vps_id, set()).add(names)
    # Add www-pair aliases per VPS
    for vps_id, names in list(domain_aliases_by_vps.items()):
        extras: set[str] = set()
        for n in list(names):
            if n.startswith("www."):
                extras.add(n[4:])
            else:
                extras.add(f"www.{n}")
        domain_aliases_by_vps[vps_id] = names | extras

    now = datetime.now(timezone.utc)
    warning_cutoff = now + timedelta(days=WARNING_DAYS)
    critical_cutoff = now + timedelta(days=CRITICAL_DAYS)

    findings: list[dict[str, Any]] = []
    assigned_domains: set[str] = set()

    for a in assignments:
        assigned_domains.add(a.domain)
        expected_vps_ids = {a.primary_vps_id, *(a.standby_vps_ids or [])}

        # missing-on-primary — but treat the www/apex pair as one
        if a.domain not in domain_aliases_by_vps.get(a.primary_vps_id, set()):
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
            if a.domain not in domain_aliases_by_vps.get(sb, set()):
                findings.append(
                    _finding(
                        "warning",
                        "missing-on-standby",
                        a.domain,
                        sb,
                        f"standby {sb} has no vhost for {a.domain} (failover will fail)",
                    )
                )

        # rogue-host (use observed-vhost domain set, not aliases — a real
        # vhost file existing somewhere unexpected is the actual concern)
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

        # cert checks per expected VPS — SAN-aware lookup
        for vps_id in expected_vps_ids:
            cert = cert_owner_by_pair.get((a.domain, vps_id))
            if cert is None and a.domain in sans_by_vps.get(vps_id, set()):
                # Defensive: shouldn't happen given how cert_owner_by_pair is built
                continue
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

    # domain-without-assignment — observed domains not in the registry.
    # Suppress when the domain is the www. or apex sibling of an already-
    # assigned domain (those are covered by SAN/server_name aliasing).
    aliased_assigned: set[str] = set()
    for d in assigned_domains:
        aliased_assigned.add(d)
        if d.startswith("www."):
            aliased_assigned.add(d[4:])
        else:
            aliased_assigned.add(f"www.{d}")
    for domain in sorted(set(domain_vps_ids) - aliased_assigned):
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
