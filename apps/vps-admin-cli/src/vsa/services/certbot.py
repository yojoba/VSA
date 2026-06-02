"""Certbot certificate issuance, renewal, listing, and health checks."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from vsa.errors import CertbotError
from vsa.services import docker

ChallengeType = Literal["http-webroot", "dns-cloudflare"]


def issue_cert(
    compose_file: Path,
    domain: str,
    *,
    include_www: bool = True,
    email: str = "ops@flowbiz.ai",
    challenge: ChallengeType = "http-webroot",
    cf_credentials_path: Path | None = None,
    cf_override_compose: Path | None = None,
    propagation_seconds: int = 60,
) -> None:
    """Issue a Let's Encrypt certificate via the requested challenge.

    For ``http-webroot``: relies on the running ``reverse-proxy-nginx`` to
    serve ``/.well-known/acme-challenge/`` from ``/var/www/certbot``.

    For ``dns-cloudflare``: a one-shot certbot container is run with the
    ``compose.dns-cloudflare.yml`` override applied so the
    ``certbot/dns-cloudflare`` image is used and ``/cf/cloudflare.ini`` is
    mounted. The credentials file at ``cf_credentials_path`` must exist on
    the host (the override mounts the directory containing it).

    ``propagation_seconds`` defaults to 60: 30s flaked for apex+www certs
    (2 ``_acme-challenge`` TXT records to propagate) on 2026-06-02 — the
    issued ``renewal/<domain>.conf`` records this value, so the certbot
    container's 12h loop reuses it on every auto-renewal.
    """
    base = ["docker", "compose", "-f", str(compose_file)]

    if challenge == "dns-cloudflare":
        if cf_credentials_path is None or not cf_credentials_path.is_file():
            raise CertbotError(
                f"Cloudflare credentials not found at {cf_credentials_path}.\n"
                "See docs/runbooks/dns01_cloudflare.md for setup."
            )
        if cf_override_compose is None or not cf_override_compose.is_file():
            raise CertbotError(
                f"DNS-01 override compose file not found: {cf_override_compose}\n"
                "Update the repo (`git pull`) so compose.dns-cloudflare.yml is present."
            )
        base.extend(["-f", str(cf_override_compose)])

    cmd_args: list[str] = ["certonly", "-d", domain]
    if include_www:
        cmd_args.extend(["-d", f"www.{domain}"])
    cmd_args.extend(
        [
            "--email", email,
            "--agree-tos", "--no-eff-email",
            "--keep-until-expiring", "--non-interactive",
        ]
    )

    if challenge == "http-webroot":
        cmd_args.extend(["--webroot", "-w", "/var/www/certbot"])
    elif challenge == "dns-cloudflare":
        cmd_args.extend(
            [
                "--dns-cloudflare",
                "--dns-cloudflare-credentials", "/cf/cloudflare.ini",
                "--dns-cloudflare-propagation-seconds", str(propagation_seconds),
            ]
        )
    else:
        raise CertbotError(f"Unknown challenge type: {challenge}")

    cmd = base + ["run", "--rm", "--entrypoint", "certbot", "certbot", *cmd_args]
    result = docker._run(cmd, check=False)
    if result.returncode != 0:
        raise CertbotError(f"Certbot failed for {domain}:\n{result.stderr}")


def delete_cert(compose_file: Path, domain: str) -> None:
    """Delete a Let's Encrypt certificate for a domain."""
    result = docker._run(
        [
            "docker", "compose", "-f", str(compose_file),
            "run", "--rm", "--entrypoint", "certbot", "certbot",
            "delete", "--cert-name", domain, "--non-interactive",
        ],
        check=False,
    )
    if result.returncode != 0:
        # Not fatal — cert may not exist
        pass


def renew(compose_file: Path) -> str:
    """Run certbot renew for all certificates."""
    result = docker._run(
        [
            "docker", "compose", "-f", str(compose_file),
            "run", "--rm", "--entrypoint", "certbot", "certbot",
            "renew", "--no-random-sleep-on-renew",
        ],
        check=False,
    )
    return result.stdout + result.stderr


def list_certs(letsencrypt_live_dir: Path) -> list[tuple[str, str]]:
    """List ``(domain, expiry_str)`` tuples by reading the host LE store.

    Replaces the old ``docker compose exec nginx openssl …`` approach which
    silently failed because ``nginx:1.25-alpine`` ships without openssl.
    The agent runs as root, which is required to traverse the mode-0700
    Let's Encrypt ``live/`` directory.
    """
    if not letsencrypt_live_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for entry in sorted(letsencrypt_live_dir.iterdir()):
        if not entry.is_dir() or entry.name == "README":
            continue
        cert = entry / "cert.pem"
        if not cert.is_file():
            continue
        result = subprocess.run(
            ["openssl", "x509", "-noout", "-enddate", "-in", str(cert)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or "=" not in result.stdout:
            continue
        expiry = result.stdout.split("=", 1)[1].strip()
        out.append((entry.name, expiry))
    return out


# ---------------------------------------------------------------------------
# Health diagnostics
# ---------------------------------------------------------------------------


def _parse_openssl_expiry(raw: str) -> datetime | None:
    """Parse the ``Aug  2 12:34:56 2026 GMT`` openssl date format."""
    raw = raw.strip()
    try:
        dt = datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def health_report(letsencrypt_dir: Path) -> list[dict[str, Any]]:
    """Return a list of issues found in the local Let's Encrypt store.

    Each finding is a dict with ``level`` (``critical``/``warning``/``info``),
    ``kind``, ``domain`` (or ``None``), and ``message``. An empty list means
    everything is healthy.
    """
    findings: list[dict[str, Any]] = []

    if not letsencrypt_dir.is_dir():
        findings.append(
            {
                "level": "critical",
                "kind": "no-letsencrypt-dir",
                "domain": None,
                "message": f"{letsencrypt_dir} does not exist",
            }
        )
        return findings

    # Account checks (prod LE account is required for renewals to work)
    prod_accounts = (
        letsencrypt_dir / "accounts" / "acme-v02.api.letsencrypt.org" / "directory"
    )
    if not prod_accounts.is_dir() or not any(prod_accounts.iterdir()):
        findings.append(
            {
                "level": "critical",
                "kind": "no-le-account",
                "domain": None,
                "message": "No prod LE account registered (renewals will fail)",
            }
        )

    # Per-cert checks
    live_dir = letsencrypt_dir / "live"
    renewal_dir = letsencrypt_dir / "renewal"
    if live_dir.is_dir():
        for entry in sorted(live_dir.iterdir()):
            if not entry.is_dir() or entry.name == "README":
                continue
            domain = entry.name

            # Symlink check (the bug we hit on vps-02)
            for fname in ("cert", "chain", "fullchain", "privkey"):
                p = entry / f"{fname}.pem"
                if p.exists() and not p.is_symlink():
                    findings.append(
                        {
                            "level": "critical",
                            "kind": "non-symlink",
                            "domain": domain,
                            "message": (
                                f"live/{domain}/{fname}.pem is a regular file, "
                                "should be a symlink into archive/ — renewal will fail"
                            ),
                        }
                    )

            # Expiry check
            cert = entry / "cert.pem"
            if cert.is_file():
                result = subprocess.run(
                    ["openssl", "x509", "-noout", "-enddate", "-in", str(cert)],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode == 0 and "=" in result.stdout:
                    expiry_raw = result.stdout.split("=", 1)[1].strip()
                    expiry_dt = _parse_openssl_expiry(expiry_raw)
                    if expiry_dt is None:
                        findings.append(
                            {
                                "level": "warning",
                                "kind": "parse-error",
                                "domain": domain,
                                "message": f"could not parse expiry: {expiry_raw!r}",
                            }
                        )
                    else:
                        days = (expiry_dt - datetime.now(timezone.utc)).days
                        if days < 0:
                            findings.append(
                                {
                                    "level": "critical",
                                    "kind": "expired",
                                    "domain": domain,
                                    "message": f"expired {-days}d ago",
                                }
                            )
                        elif days <= 14:
                            findings.append(
                                {
                                    "level": "critical",
                                    "kind": "expiring",
                                    "domain": domain,
                                    "message": f"expires in {days}d",
                                }
                            )
                        elif days <= 30:
                            findings.append(
                                {
                                    "level": "warning",
                                    "kind": "expiring-soon",
                                    "domain": domain,
                                    "message": f"expires in {days}d",
                                }
                            )

            # Renewal config check
            renewal_conf = renewal_dir / f"{domain}.conf"
            if not renewal_conf.is_file():
                findings.append(
                    {
                        "level": "warning",
                        "kind": "orphan",
                        "domain": domain,
                        "message": "no matching renewal/<domain>.conf (cert will not auto-renew)",
                    }
                )

    return findings
