"""Multi-VPS agent commands (Phase 6 — hub-and-agent model)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()

AGENT_ENV_PATH = Path("/etc/vsa/agent.env")


@app.command()
def register(
    hub_url: str = typer.Option(..., "--hub-url", help="Dashboard API URL"),
    token: str = typer.Option(..., "--token", help="Pre-shared API token"),
) -> None:
    """Register this VPS as an agent with the central dashboard."""
    AGENT_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_ENV_PATH.write_text(
        f"VSA_HUB_URL={hub_url}\n"
        f"VSA_AGENT_TOKEN={token}\n"
    )
    console.print(f"[green]Agent registered.[/green] Config written to {AGENT_ENV_PATH}")
    console.print("Start the agent with: vsa agent start")


@app.command()
def start() -> None:
    """Start the agent loop (heartbeat, container sync, cert check, audit sync)."""
    if not AGENT_ENV_PATH.exists():
        console.print("[red]Agent not registered. Run 'vsa agent register' first.[/red]")
        raise typer.Exit(1)

    console.print("[yellow]Agent start is intended to be run via systemd timer.[/yellow]")
    console.print("Install with: sudo cp infra/systemd/vsa-agent.* /etc/systemd/system/")
    console.print("             sudo systemctl enable --now vsa-agent.timer")


@app.command()
def status() -> None:
    """Show agent registration status."""
    if AGENT_ENV_PATH.exists():
        content = AGENT_ENV_PATH.read_text()
        for line in content.strip().splitlines():
            key, _, value = line.partition("=")
            if "TOKEN" in key:
                value = value[:8] + "..."
            console.print(f"  {key} = {value}")
    else:
        console.print("[yellow]Agent not registered.[/yellow]")
