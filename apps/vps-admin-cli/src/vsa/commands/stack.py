"""Docker Compose stack lifecycle commands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from vsa.audit import audit
from vsa.config import get_config
from vsa.services import docker

app = typer.Typer(no_args_is_help=True)
console = Console()


def _resolve_compose(name: Optional[str]) -> Path:
    """Resolve a stack name to its compose.yml path."""
    cfg = get_config()
    if name:
        compose = cfg.stack_dir / name / "compose.yml"
    else:
        compose = Path.cwd() / "compose.yml"
    if not compose.exists():
        console.print(f"[red]compose.yml not found at {compose}[/red]")
        raise typer.Exit(1)
    return compose


@app.command()
def up(
    name: Optional[str] = typer.Argument(None, help="Stack name (or run from stack dir)"),
) -> None:
    """Start a stack (docker compose up -d --build)."""
    compose = _resolve_compose(name)
    with audit("stack.up", target=name or compose.parent.name):
        docker.compose_up(compose)
        console.print(f"[green]Stack started: {compose.parent.name}[/green]")


@app.command()
def down(
    name: Optional[str] = typer.Argument(None, help="Stack name"),
) -> None:
    """Stop a stack (docker compose down)."""
    compose = _resolve_compose(name)
    with audit("stack.down", target=name or compose.parent.name):
        docker.compose_down(compose)
        console.print(f"[yellow]Stack stopped: {compose.parent.name}[/yellow]")


@app.command()
def logs(
    name: Optional[str] = typer.Argument(None, help="Stack name"),
    tail: int = typer.Option(200, help="Number of lines to show"),
) -> None:
    """Show stack logs."""
    compose = _resolve_compose(name)
    docker.compose_logs(compose, tail=tail)


@app.command()
def ps(
    name: Optional[str] = typer.Argument(None, help="Stack name"),
) -> None:
    """Show stack container status."""
    compose = _resolve_compose(name)
    output = docker.compose_ps(compose)
    console.print(output)


@app.command()
def new(
    name: str = typer.Argument(help="New stack name"),
) -> None:
    """Create a new stack from template."""
    cfg = get_config()
    stack_dir = cfg.stack_dir / name

    with audit("stack.new", target=name):
        if stack_dir.exists():
            console.print(f"[red]Stack directory already exists: {stack_dir}[/red]")
            raise typer.Exit(1)

        stack_dir.mkdir(parents=True)

        # compose.yml
        compose = stack_dir / "compose.yml"
        compose.write_text(f"""services:
  app:
    image: nginx:1.25-alpine
    restart: unless-stopped
    networks:
      - {name}-net
      - flowbiz_ext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

networks:
  flowbiz_ext:
    external: true
  {name}-net: {{}}
""")

        # Makefile
        makefile = stack_dir / "Makefile"
        makefile.write_text(""".PHONY: up down logs ps
up:
\tdocker compose -f compose.yml up -d --build
down:
\tdocker compose -f compose.yml down
logs:
\tdocker compose -f compose.yml logs -f --tail=200
ps:
\tdocker compose -f compose.yml ps
""")

        # .env.example
        (stack_dir / ".env.example").write_text("# Environment variables for this stack\n")

        # README.md
        (stack_dir / "README.md").write_text(f"""# {name}

## Deploy

```bash
mkdir -p /srv/flowbiz/{name}/{{data,env,logs}}
cp .env.example /srv/flowbiz/{name}/env/.env
docker compose up -d
```
""")

        console.print(f"[green]Stack created at {stack_dir}[/green]")
