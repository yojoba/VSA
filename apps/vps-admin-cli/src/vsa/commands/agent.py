"""Multi-VPS agent commands (Phase 6 — hub-and-agent model)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(no_args_is_help=True)
console = Console()

AGENT_ENV_PATH = Path("/etc/vsa/agent.env")


def _load_agent_env() -> dict[str, str]:
    """Load hub URL and token from agent.env."""
    if not AGENT_ENV_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in AGENT_ENV_PATH.read_text().strip().splitlines():
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


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
    """Run one sync cycle (heartbeat, containers, certs, domains, audit)."""
    env = _load_agent_env()
    if not env:
        console.print("[red]Agent not registered. Run 'vsa agent register' first.[/red]")
        raise typer.Exit(1)

    hub_url = env.get("VSA_HUB_URL", "")
    token = env.get("VSA_AGENT_TOKEN", "")
    if not hub_url or not token:
        console.print("[red]Missing VSA_HUB_URL or VSA_AGENT_TOKEN in agent.env[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Syncing with {hub_url} ...[/bold]")

    from vsa.services.agent_sync import run_sync

    run_sync(hub_url, token)


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
