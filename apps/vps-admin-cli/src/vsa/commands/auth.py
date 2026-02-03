"""HTTP Basic Auth management commands."""

from __future__ import annotations

import shutil

import typer
from rich.console import Console
from rich.table import Table

from vsa.audit import audit
from vsa.config import get_config
from vsa.services import htpasswd, nginx

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def add(
    domain: str = typer.Option(..., help="Domain to protect"),
    user: str = typer.Option(..., help="Username"),
    password: str = typer.Option(..., prompt=True, hide_input=True, help="Password"),
) -> None:
    """Add HTTP Basic Auth to a domain."""
    cfg = get_config()

    with audit("auth.add", target=domain, user=user):
        # Verify vhost exists
        vhost_file = cfg.repo_vhost_dir / f"{domain}.conf"
        if not vhost_file.exists():
            console.print(f"[red]Error:[/red] Vhost for {domain} not found at {vhost_file}")
            console.print("Provision the site first with: vsa site provision")
            raise typer.Exit(1)

        # Generate htpasswd file with bcrypt
        auth_file = cfg.repo_auth_dir / f"{domain}.htpasswd"
        console.print(f"[bold][1/3][/bold] Generating bcrypt htpasswd for {user}")
        htpasswd.write_htpasswd_file(auth_file, user, password)

        # Update vhost to include auth directives (if not already present)
        console.print("[bold][2/3][/bold] Updating vhost config")
        content = vhost_file.read_text()
        if "auth_basic" not in content:
            content = content.replace(
                "include /etc/nginx/snippets/security_headers.conf;",
                "include /etc/nginx/snippets/security_headers.conf;\n\n"
                "  # HTTP Basic Auth\n"
                f'  auth_basic "Restricted Access";\n'
                f"  auth_basic_user_file /etc/nginx/auth/{domain}.htpasswd;",
            )
            vhost_file.write_text(content)

        # Sync auth file and vhost to mount dirs
        console.print("[bold][3/3][/bold] Deploying and reloading NGINX")
        cfg.mount_auth_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(auth_file), str(cfg.mount_auth_dir / f"{domain}.htpasswd"))
        shutil.copy2(str(vhost_file), str(cfg.mount_vhost_dir / f"{domain}.conf"))
        nginx.reload(cfg.reverse_proxy_compose)

        console.print(f"\n[green]Basic auth enabled for {domain}[/green] (user: {user})")


@app.command()
def remove(
    domain: str = typer.Option(..., help="Domain to unprotect"),
) -> None:
    """Remove HTTP Basic Auth from a domain."""
    cfg = get_config()

    with audit("auth.remove", target=domain):
        vhost_file = cfg.repo_vhost_dir / f"{domain}.conf"
        if not vhost_file.exists():
            console.print(f"[red]Error:[/red] Vhost not found: {vhost_file}")
            raise typer.Exit(1)

        # Remove auth directives from vhost
        content = vhost_file.read_text()
        lines = content.splitlines()
        filtered = [
            line for line in lines
            if "auth_basic " not in line
            and "auth_basic_user_file " not in line
            and line.strip() != "# HTTP Basic Auth"
        ]
        vhost_file.write_text("\n".join(filtered) + "\n")

        # Remove htpasswd file
        auth_file = cfg.repo_auth_dir / f"{domain}.htpasswd"
        if auth_file.exists():
            auth_file.unlink()

        console.print(f"[green]Basic auth removed for {domain}.[/green]")
        console.print("[yellow]Run 'vsa vhost sync' to apply changes.[/yellow]")


@app.command(name="list")
def list_auth() -> None:
    """List domains with Basic Auth enabled."""
    cfg = get_config()
    auth_dir = cfg.repo_auth_dir

    if not auth_dir.exists():
        console.print("No auth directory found.")
        return

    table = Table(title="HTTP Basic Auth")
    table.add_column("Domain", style="cyan")
    table.add_column("Users", style="green")

    for f in sorted(auth_dir.glob("*.htpasswd")):
        domain = f.stem
        users = htpasswd.read_htpasswd_users(f)
        table.add_row(domain, ", ".join(users))

    console.print(table)
