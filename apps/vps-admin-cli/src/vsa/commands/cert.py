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


@app.command()
def issue(
    domain: str = typer.Option(..., help="Domain to issue certificate for"),
    no_www: bool = typer.Option(False, "--no-www", help="Skip www subdomain"),
) -> None:
    """Issue a Let's Encrypt certificate for a domain."""
    cfg = get_config()

    with audit("cert.issue", target=domain):
        certbot.issue_cert(
            cfg.reverse_proxy_compose,
            domain,
            include_www=not no_www,
            email=cfg.certbot_email,
        )
        console.print(f"[green]Certificate issued for {domain}[/green]")


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
    """Show certificate status for all domains."""
    cfg = get_config()
    output = certbot.list_certs(cfg.reverse_proxy_compose)

    table = Table(title="SSL Certificates")
    table.add_column("Domain", style="cyan")
    table.add_column("Expires", style="yellow")

    for line in output.strip().splitlines():
        line = line.strip()
        if "|" in line:
            domain, expiry = line.split("|", 1)
            table.add_row(domain.strip(), expiry.strip())

    console.print(table)


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
