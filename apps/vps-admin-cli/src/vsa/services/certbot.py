"""Certbot certificate issuance and renewal."""

from __future__ import annotations

from pathlib import Path

from vsa.errors import CertbotError
from vsa.services import docker


def issue_cert(
    compose_file: Path,
    domain: str,
    *,
    include_www: bool = True,
    email: str = "ops@flowbiz.ai",
) -> None:
    """Issue a Let's Encrypt certificate via HTTP-01 webroot challenge."""
    cmd = [
        "docker", "compose", "-f", str(compose_file),
        "run", "--rm", "--entrypoint", "certbot", "certbot",
        "certonly", "--webroot", "-w", "/var/www/certbot",
        "-d", domain,
    ]
    if include_www:
        cmd.extend(["-d", f"www.{domain}"])
    cmd.extend([
        "--email", email,
        "--agree-tos", "--no-eff-email",
        "--keep-until-expiring", "--non-interactive",
    ])

    result = docker._run(cmd, check=False)
    if result.returncode != 0:
        raise CertbotError(f"Certbot failed for {domain}:\n{result.stderr}")


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


def list_certs(compose_file: Path) -> str:
    """List certificate expiry info from inside the NGINX container."""
    result = docker.compose_exec(
        compose_file, "nginx",
        "sh", "-c",
        """for dir in /etc/letsencrypt/live/*/; do
            [ -d "$dir" ] || continue
            domain=$(basename "$dir")
            [ "$domain" = "README" ] && continue
            cert="$dir/cert.pem"
            if [ -f "$cert" ]; then
                expiry=$(openssl x509 -noout -enddate -in "$cert" 2>/dev/null | cut -d= -f2)
                echo "$domain|$expiry"
            fi
        done""",
    )
    return result.stdout
