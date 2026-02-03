"""Site provisioning and management commands."""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from vsa_common import SiteConfig
from vsa.audit import audit
from vsa.config import get_config
from vsa.services import certbot, docker, network, nginx, vhost_renderer

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def provision(
    domain: str = typer.Option(..., help="Domain name (e.g., example.com)"),
    port: int = typer.Option(3000, help="Internal container port"),
    container: Optional[str] = typer.Option(None, help="Container name (auto-detected if --detect)"),
    detect: bool = typer.Option(False, help="Auto-detect container from published port"),
    external_port: Optional[int] = typer.Option(None, help="Published host port (for --detect)"),
    no_www: bool = typer.Option(False, "--no-www", help="Skip www subdomain"),
    skip_cert: bool = typer.Option(False, "--skip-cert", help="Skip SSL certificate issuance"),
) -> None:
    """Provision a site behind the reverse proxy with SSL."""
    cfg = get_config()

    with audit("site.provision", target=domain, port=port, container=container or "auto") as event:
        # Step 0: Detect container if requested
        if detect:
            if external_port is None:
                typer.echo("Error: --external-port required with --detect", err=True)
                raise typer.Exit(1)
            container = docker.find_container_by_port(external_port)
            console.print(f"[green]Detected container:[/green] {container}")
            event.params["container"] = container

        if not container:
            typer.echo("Error: --container or --detect --external-port required", err=True)
            raise typer.Exit(1)

        site = SiteConfig(
            domain=domain,
            container=container,
            port=port,
            include_www=not no_www,
        )

        # Step 1: Ensure mount directories
        console.print("[bold][1/6][/bold] Ensuring reverse-proxy mount directories")
        for d in [cfg.mount_vhost_dir, cfg.mount_snippets_dir, cfg.mount_auth_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Step 2: Connect container to network
        console.print(f"[bold][2/6][/bold] Connecting {container} to {cfg.docker_network}")
        network.connect_container(container)

        # Step 3: Write HTTP-only vhost for ACME
        console.print("[bold][3/6][/bold] Generating HTTP-only vhost for ACME challenge")
        http_config = vhost_renderer.render_http_vhost(site)
        vhost_path = cfg.repo_vhost_dir / f"{domain}.conf"
        vhost_renderer.write_vhost(vhost_path, http_config)

        # Step 4: Deploy and reload
        console.print("[bold][4/6][/bold] Deploying HTTP vhost and reloading NGINX")
        _sync_single_vhost(cfg, domain)
        docker.compose_up(cfg.reverse_proxy_compose)
        nginx.reload_unsafe(cfg.reverse_proxy_compose)

        # Step 5: Issue certificate
        if not skip_cert:
            console.print(f"[bold][5/6][/bold] Issuing Let's Encrypt certificate for {domain}")
            certbot.issue_cert(
                cfg.reverse_proxy_compose,
                domain,
                include_www=not no_www,
                email=cfg.certbot_email,
            )
        else:
            console.print("[bold][5/6][/bold] Skipping certificate issuance (--skip-cert)")

        # Step 6: Write final HTTPS vhost and reload
        console.print("[bold][6/6][/bold] Generating HTTPS vhost and reloading NGINX")
        https_config = vhost_renderer.render_https_vhost(site)
        vhost_renderer.write_vhost(vhost_path, https_config)
        _sync_single_vhost(cfg, domain)
        nginx.reload(cfg.reverse_proxy_compose)

        console.print(f"\n[green bold]Done![/green bold] https://{domain}/ → {container}:{port}")


@app.command()
def unprovision(
    domain: str = typer.Option(..., help="Domain to unprovision"),
) -> None:
    """Remove a site from the reverse proxy (vhost + auth files)."""
    cfg = get_config()

    with audit("site.unprovision", target=domain):
        vhost_file = cfg.repo_vhost_dir / f"{domain}.conf"
        auth_file = cfg.repo_auth_dir / f"{domain}.htpasswd"

        if vhost_file.exists():
            vhost_file.unlink()
            console.print(f"Removed vhost: {vhost_file}")
        else:
            console.print(f"No vhost found: {vhost_file}")

        if auth_file.exists():
            auth_file.unlink()
            console.print(f"Removed auth file: {auth_file}")

        console.print("\n[yellow]Run 'vsa vhost sync' to apply changes.[/yellow]")


@app.command(name="list")
def list_sites() -> None:
    """List all provisioned sites."""
    cfg = get_config()
    vhost_dir = cfg.repo_vhost_dir

    if not vhost_dir.exists():
        console.print("No vhost directory found.")
        return

    table = Table(title="Provisioned Sites")
    table.add_column("Domain", style="cyan")
    table.add_column("Auth", style="yellow")

    for conf in sorted(vhost_dir.glob("*.conf")):
        domain = conf.stem
        has_auth = (cfg.repo_auth_dir / f"{domain}.htpasswd").exists()
        table.add_row(domain, "yes" if has_auth else "no")

    console.print(table)


def _sync_single_vhost(cfg, domain: str) -> None:
    """Copy a single vhost from repo to mount directory."""
    src = cfg.repo_vhost_dir / f"{domain}.conf"
    dst = cfg.mount_vhost_dir / f"{domain}.conf"
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
