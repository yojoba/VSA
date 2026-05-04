"""NGINX vhost sync and management commands."""

from __future__ import annotations

import shutil
import subprocess

import typer
from rich.console import Console
from rich.syntax import Syntax

from vsa.audit import audit
from vsa.config import get_config
from vsa.services import nginx

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def sync() -> None:
    """Sync vhost and snippet files from repo to mounted directory, then reload NGINX.

    Example:

        vsa vhost sync

    Rsyncs `stacks/reverse-proxy/nginx/{conf.d,snippets}/` →
    `/srv/flowbiz/reverse-proxy/nginx/...` (the bind-mount nginx loads).
    Uses `--delete` so files removed from the repo are removed from the
    mount too. Validates with `nginx -t` before reloading.

    For multi-VPS: run on each VPS separately, or from the hub:
    `vsa fleet vhost-sync --vps vps-X`.
    """
    cfg = get_config()

    with audit("vhost.sync"):
        # Sync vhosts
        console.print("[bold][1/3][/bold] Syncing vhost files")
        _rsync(cfg.repo_vhost_dir, cfg.mount_vhost_dir)

        # Sync snippets
        console.print("[bold][2/3][/bold] Syncing snippet files")
        _rsync(cfg.repo_snippets_dir, cfg.mount_snippets_dir)

        # Validate and reload
        console.print("[bold][3/3][/bold] Validating and reloading NGINX")
        nginx.reload(cfg.reverse_proxy_compose)

        console.print("[green]Vhosts synced and NGINX reloaded.[/green]")


@app.command(name="list")
def list_vhosts() -> None:
    """List all vhost config files.

    Example:

        vsa vhost list
    """
    cfg = get_config()
    vhost_dir = cfg.repo_vhost_dir

    if not vhost_dir.exists():
        console.print("No vhost directory found.")
        return

    for conf in sorted(vhost_dir.glob("*.conf")):
        console.print(f"  {conf.stem}")


@app.command()
def show(
    domain: str = typer.Argument(help="Domain name to show config for"),
) -> None:
    """Display the NGINX vhost config for a domain.

    Example:

        vsa vhost show dashboard.flowbiz.ai
    """
    cfg = get_config()
    vhost_file = cfg.repo_vhost_dir / f"{domain}.conf"

    if not vhost_file.exists():
        console.print(f"[red]No vhost found for {domain}[/red]")
        raise typer.Exit(1)

    content = vhost_file.read_text()
    syntax = Syntax(content, "nginx", theme="monokai")
    console.print(syntax)


def _rsync(src: object, dst: object) -> None:
    """Rsync a directory (with --delete to remove orphans)."""
    dst_path = str(dst)
    subprocess.run(
        ["sudo", "rsync", "-av", "--delete", f"{src}/", f"{dst_path}/"],
        check=True,
        capture_output=True,
        text=True,
    )
