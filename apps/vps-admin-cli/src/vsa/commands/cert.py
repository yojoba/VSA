"""SSL certificate management commands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from vsa.audit import audit
from vsa.config import get_config
from vsa.services import certbot, nginx

app = typer.Typer(no_args_is_help=True)
console = Console()


_VALID_CHALLENGES = ("http-webroot", "dns-cloudflare")


@app.command()
def issue(
    domain: str = typer.Option(..., help="Domain to issue certificate for"),
    no_www: bool = typer.Option(False, "--no-www", help="Skip www subdomain"),
    challenge: str = typer.Option(
        "http-webroot",
        "--challenge",
        help=(
            "Let's Encrypt challenge type. 'http-webroot' (default) requires "
            "the running reverse-proxy to serve /.well-known/acme-challenge/. "
            "'dns-cloudflare' uses the Cloudflare API token at "
            "/srv/flowbiz/reverse-proxy/cloudflare/cloudflare.ini — required "
            "for warm-standby VPS where domains DNS-resolve elsewhere."
        ),
    ),
) -> None:
    """Issue a Let's Encrypt certificate for a domain."""
    if challenge not in _VALID_CHALLENGES:
        raise typer.BadParameter(
            f"Unknown challenge {challenge!r}. Must be one of: "
            f"{', '.join(_VALID_CHALLENGES)}"
        )

    cfg = get_config()

    cf_credentials = cfg.cloudflare_credentials if challenge == "dns-cloudflare" else None
    cf_override = (
        cfg.reverse_proxy_dns_cloudflare_compose if challenge == "dns-cloudflare" else None
    )

    with audit(
        "cert.issue",
        target=domain,
        params={"challenge": challenge, "include_www": not no_www},
    ):
        certbot.issue_cert(
            cfg.reverse_proxy_compose,
            domain,
            include_www=not no_www,
            email=cfg.certbot_email,
            challenge=challenge,
            cf_credentials_path=cf_credentials,
            cf_override_compose=cf_override,
        )
        console.print(f"[green]Certificate issued for {domain} via {challenge}[/green]")


@app.command()
def renew() -> None:
    """Renew all expiring certificates."""
    cfg = get_config()

    with audit("cert.renew"):
        output = certbot.renew(cfg.reverse_proxy_compose)
        console.print(output)

        console.print("\nReloading NGINX...")
        nginx.reload(cfg.reverse_proxy_compose)
        console.print("[green]Done.[/green]")


@app.command()
def status() -> None:
    """Show certificate status for all domains (reads host LE store)."""
    cfg = get_config()
    rows = certbot.list_certs(cfg.letsencrypt_live_dir)

    table = Table(title=f"SSL Certificates ({cfg.vps_id})")
    table.add_column("Domain", style="cyan")
    table.add_column("Expires", style="yellow")

    for domain, expiry in rows:
        table.add_row(domain, expiry)

    if not rows:
        console.print("[dim]No certificates found.[/dim]")
        return
    console.print(table)


@app.command()
def health() -> None:
    """Diagnose the local Let's Encrypt store for known issues.

    Flags broken symlinks (the vps-02 footgun), missing prod LE account,
    expired or soon-to-expire certs, and certs without a matching renewal
    config. Exits non-zero if any critical finding (suitable for cron).
    """
    cfg = get_config()
    findings = certbot.health_report(cfg.letsencrypt_dir)

    if not findings:
        console.print(f"[green]✓ All certificates healthy on {cfg.vps_id}.[/green]")
        return

    critical = sum(1 for f in findings if f["level"] == "critical")
    warning = sum(1 for f in findings if f["level"] == "warning")

    table = Table(title=f"Certificate Health ({cfg.vps_id})")
    table.add_column("Level", style="bold")
    table.add_column("Kind")
    table.add_column("Domain")
    table.add_column("Issue")

    for f in findings:
        level_color = {"critical": "red", "warning": "yellow"}.get(f["level"], "white")
        table.add_row(
            f"[{level_color}]{f['level']}[/{level_color}]",
            f["kind"],
            f["domain"] or "-",
            f["message"],
        )

    console.print(table)
    console.print(
        f"[bold]Summary:[/bold] {critical} critical, {warning} warning"
    )
    if critical > 0:
        raise typer.Exit(1)


@app.command(name="install-cron")
def install_cron() -> None:
    """Install a daily cron job for certificate renewal checks."""
    import subprocess

    with audit("cert.install-cron"):
        cron_line = '0 3 * * * vsa cert renew >> /var/log/vsa/cert-renewal.log 2>&1'

        # Check if already installed
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, check=False,
        )
        existing = result.stdout if result.returncode == 0 else ""

        if "vsa cert renew" in existing:
            console.print("Cron job already installed, updating...")
            lines = [l for l in existing.splitlines() if "vsa cert renew" not in l]
            lines.append(cron_line)
        else:
            console.print("Adding new cron job...")
            lines = existing.splitlines() + [cron_line]

        new_crontab = "\n".join(lines) + "\n"
        subprocess.run(
            ["crontab", "-"],
            input=new_crontab, text=True, check=True,
        )
        console.print("[green]Certificate monitoring cron installed (daily at 3:00 AM).[/green]")
