"""Site provisioning and management commands."""

from __future__ import annotations

import re
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


def _extract_container_from_vhost(cfg, domain: str) -> str | None:
    """Parse the vhost config to extract the upstream container name."""
    vhost_file = cfg.repo_vhost_dir / f"{domain}.conf"
    if not vhost_file.exists():
        return None
    return _extract_container_from_content(vhost_file.read_text())


def _extract_container_from_content(content: str) -> str | None:
    """Extract container name from vhost config content."""
    # Match 'set $upstream container:port;'
    match = re.search(r"set\s+\$upstream\s+([^:\s]+)", content)
    if match:
        return match.group(1)
    # Fallback: match 'proxy_pass http://container:port'
    match = re.search(r"proxy_pass\s+https?://([^:/\s]+)", content)
    return match.group(1) if match else None


def _find_domains_for_container(cfg, container: str, exclude_domain: str) -> list[str]:
    """Find all other domains that point to the same container."""
    other_domains = []
    for conf in cfg.repo_vhost_dir.glob("*.conf"):
        domain = conf.stem
        if domain == exclude_domain:
            continue
        c = _extract_container_from_content(conf.read_text())
        if c == container:
            other_domains.append(domain)
    return sorted(other_domains)


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
    keep_container: bool = typer.Option(False, "--keep-container", help="Don't stop/remove the container"),
    keep_cert: bool = typer.Option(False, "--keep-cert", help="Don't delete the Let's Encrypt certificate"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Remove a site and all its underlying resources (vhost, auth, cert, logs, container)."""
    cfg = get_config()

    # Detect container from the vhost config before deleting it
    container_name = _extract_container_from_vhost(cfg, domain)

    # Check if other domains share the same container
    remove_container = False
    shared_domains: list[str] = []
    if container_name:
        shared_domains = _find_domains_for_container(cfg, container_name, domain)
        if shared_domains and not keep_container:
            console.print(
                f"\n[bold yellow]Warning:[/bold yellow] Container [cyan]{container_name}[/cyan] "
                f"is also used by:"
            )
            for d in shared_domains:
                console.print(f"  - {d}")
            console.print()
            if yes:
                # Non-interactive: keep the container when shared
                remove_container = False
                console.print("  Container will be kept (shared with other domains).")
            else:
                remove_container = typer.confirm(
                    "Remove the container anyway? (this will break the other domains)",
                    default=False,
                )
        elif not keep_container:
            remove_container = True

    if not yes:
        console.print(f"\n[bold red]About to unprovision:[/bold red]")
        console.print(f"  Domain: {domain}")
        if container_name:
            if remove_container:
                console.print(f"  Container: {container_name} [red](will be stopped and removed)[/red]")
            else:
                console.print(f"  Container: {container_name} [green](will be kept)[/green]")
        if not keep_cert:
            console.print(f"  Certificate: will be deleted")
        console.print(f"  Vhost, auth, and log files: will be deleted")
        console.print()
        if not typer.confirm("Continue?"):
            raise typer.Abort()

    with audit(
        "site.unprovision",
        target=domain,
        container=container_name or "none",
        remove_container=remove_container,
    ):
        step = 0
        total = 6

        # Step 1: Remove vhost config (repo + mount)
        step += 1
        console.print(f"[bold][{step}/{total}][/bold] Removing vhost config")
        for vhost_dir in [cfg.repo_vhost_dir, cfg.mount_vhost_dir]:
            vhost_file = vhost_dir / f"{domain}.conf"
            if vhost_file.exists():
                vhost_file.unlink()
                console.print(f"  Removed: {vhost_file}")

        # Step 2: Remove auth/htpasswd (repo + mount)
        step += 1
        console.print(f"[bold][{step}/{total}][/bold] Removing auth files")
        for auth_dir in [cfg.repo_auth_dir, cfg.mount_auth_dir]:
            auth_file = auth_dir / f"{domain}.htpasswd"
            if auth_file.exists():
                auth_file.unlink()
                console.print(f"  Removed: {auth_file}")

        # Step 3: Delete Let's Encrypt certificate
        step += 1
        if not keep_cert:
            console.print(f"[bold][{step}/{total}][/bold] Deleting Let's Encrypt certificate")
            certbot.delete_cert(cfg.reverse_proxy_compose, domain)
            console.print(f"  Deleted cert for: {domain}")
        else:
            console.print(f"[bold][{step}/{total}][/bold] Skipping certificate deletion (--keep-cert)")

        # Step 4: Remove per-domain access log
        step += 1
        console.print(f"[bold][{step}/{total}][/bold] Removing access log files")
        log_dir = cfg.srv_base / "reverse-proxy" / "logs" / "domains"
        for pattern in [f"{domain}.access.json", f"{domain}.access.log"]:
            log_file = log_dir / pattern
            if log_file.exists():
                log_file.unlink()
                console.print(f"  Removed: {log_file}")

        # Step 5: Handle container
        step += 1
        if container_name:
            if remove_container:
                console.print(f"[bold][{step}/{total}][/bold] Stopping and removing container: {container_name}")
                docker.network_disconnect(cfg.docker_network, container_name)
                docker.container_stop_remove(container_name)
                console.print(f"  Removed: {container_name}")
            elif shared_domains:
                console.print(
                    f"[bold][{step}/{total}][/bold] Keeping container {container_name} "
                    f"(shared with {', '.join(shared_domains)})"
                )
            elif keep_container:
                console.print(f"[bold][{step}/{total}][/bold] Keeping container {container_name} (--keep-container)")
                docker.network_disconnect(cfg.docker_network, container_name)
                console.print(f"  Disconnected from {cfg.docker_network}")
            else:
                console.print(f"[bold][{step}/{total}][/bold] No action needed for container")
        else:
            console.print(f"[bold][{step}/{total}][/bold] No container detected (skipping)")

        # Step 6: Reload NGINX
        step += 1
        console.print(f"[bold][{step}/{total}][/bold] Reloading NGINX")
        try:
            nginx.reload(cfg.reverse_proxy_compose)
            console.print("  NGINX reloaded successfully")
        except Exception as exc:
            console.print(f"  [yellow]NGINX reload warning: {exc}[/yellow]")

        console.print(f"\n[green bold]Done![/green bold] {domain} fully unprovisioned.")


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
