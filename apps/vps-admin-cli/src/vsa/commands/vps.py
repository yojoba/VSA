"""VPS node management — list, add, and remove VPS nodes from the dashboard."""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.table import Table

from vsa.audit import audit
from vsa.commands.agent import AGENT_ENV_PATH, _load_agent_env

app = typer.Typer(no_args_is_help=True)
console = Console()


def _hub_client() -> tuple[str, dict[str, str]]:
    """Return (hub_url, headers) or exit if agent is not registered."""
    env = _load_agent_env()
    if not env:
        console.print("[red]Agent not registered. Run 'vsa agent register' first.[/red]")
        raise typer.Exit(1)

    hub_url = env.get("VSA_HUB_URL", "")
    token = env.get("VSA_AGENT_TOKEN", "")
    if not hub_url or not token:
        console.print("[red]Missing VSA_HUB_URL or VSA_AGENT_TOKEN in agent.env[/red]")
        raise typer.Exit(1)

    return hub_url, {"Authorization": f"Bearer {token}"}


@app.command("list")
def list_vps() -> None:
    """List all registered VPS nodes."""
    hub_url, headers = _hub_client()

    resp = httpx.get(f"{hub_url}/vps", headers=headers, timeout=30.0)
    resp.raise_for_status()
    nodes = resp.json()

    if not nodes:
        console.print("[yellow]No VPS nodes registered.[/yellow]")
        return

    table = Table(title="VPS Nodes")
    table.add_column("VPS ID", style="bold")
    table.add_column("Hostname")
    table.add_column("IP Address")
    table.add_column("Status")
    table.add_column("Last Seen")

    for n in nodes:
        status_style = "green" if n.get("status") == "active" else "yellow"
        table.add_row(
            n.get("vps_id", ""),
            n.get("hostname", ""),
            n.get("ip_address", ""),
            f"[{status_style}]{n.get('status', '')}[/{status_style}]",
            n.get("last_seen", "") or "",
        )

    console.print(table)


@app.command("add")
def add_vps(
    vps_id: str = typer.Option(..., "--id", help="Unique VPS identifier (e.g. 'vps-02')"),
    hostname: str = typer.Option("", "--hostname", "-h", help="Hostname of the VPS"),
    ip_address: str = typer.Option("", "--ip", help="IP address of the VPS"),
) -> None:
    """Register a new VPS node in the dashboard."""
    hub_url, headers = _hub_client()

    with audit("vps.add", target=vps_id, hostname=hostname, ip_address=ip_address):
        resp = httpx.post(
            f"{hub_url}/agent/heartbeat",
            headers=headers,
            json={
                "vps_id": vps_id,
                "hostname": hostname,
                "ip_address": ip_address,
            },
            timeout=30.0,
        )
        resp.raise_for_status()

    console.print(f"[green]VPS '{vps_id}' registered.[/green]")
    console.print(
        f"On the new VPS, run:\n"
        f"  vsa agent register --hub-url {hub_url} --token <token>\n"
        f"  vsa agent start"
    )


@app.command("remove")
def remove_vps(
    vps_id: str = typer.Argument(..., help="VPS ID to remove (e.g. 'vps-02')"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a VPS node and all its associated data from the dashboard."""
    hub_url, headers = _hub_client()

    if not yes:
        typer.confirm(
            f"Remove VPS '{vps_id}' and all its domains/snapshots/traffic data?",
            abort=True,
        )

    with audit("vps.remove", target=vps_id):
        resp = httpx.delete(
            f"{hub_url}/agent/vps/{vps_id}",
            headers=headers,
            timeout=30.0,
        )
        if resp.status_code == 404:
            console.print(f"[yellow]VPS '{vps_id}' not found.[/yellow]")
            raise typer.Exit(1)
        resp.raise_for_status()

    console.print(f"[green]VPS '{vps_id}' removed.[/green]")
