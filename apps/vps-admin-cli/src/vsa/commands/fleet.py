"""Fleet-aware commands — talk to the hub API to manage cross-VPS state."""

from __future__ import annotations

import getpass
import time
from collections import defaultdict

import typer
from rich.console import Console
from rich.table import Table

from vsa.audit import audit
from vsa.services import hub_client
from vsa.services.hub_client import HubClientError

app = typer.Typer(no_args_is_help=True)
console = Console()


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


@app.command()
def assign(
    domain: str = typer.Option(..., help="Domain to assign"),
    primary: str = typer.Option(..., "--primary", help="vps_id that owns the domain (active)"),
    standbys: str = typer.Option(
        "",
        "--standbys",
        help="Comma-separated list of vps_ids that warm-stand-by this domain",
    ),
    notes: str = typer.Option("", "--notes", help="Free-form notes"),
) -> None:
    """Set or update the primary + standby VPS for a domain."""
    standby_list = _split_csv(standbys)

    with audit(
        "fleet.assign",
        target=domain,
        params={
            "primary_vps_id": primary,
            "standby_vps_ids": standby_list,
        },
    ):
        result = hub_client.upsert_assignment(
            domain,
            primary_vps_id=primary,
            standby_vps_ids=standby_list,
            notes=notes,
        )
        console.print(
            f"[green]✓[/green] {domain}: primary=[bold]{result['primary_vps_id']}[/bold]"
            f" standbys={result['standby_vps_ids']}"
        )


@app.command(name="list")
def list_assignments() -> None:
    """List all domain assignments across the fleet."""
    rows = hub_client.list_assignments()

    if not rows:
        console.print("[dim]No assignments yet. Use `vsa fleet assign …` to add some.[/dim]")
        return

    table = Table(title=f"Domain Assignments ({len(rows)})")
    table.add_column("Domain", style="cyan")
    table.add_column("Primary", style="green")
    table.add_column("Standbys", style="yellow")
    table.add_column("Notes", style="dim")
    for r in rows:
        table.add_row(
            r["domain"],
            r["primary_vps_id"],
            ", ".join(r["standby_vps_ids"]) or "-",
            r.get("notes", "")[:60],
        )
    console.print(table)


@app.command()
def show(domain: str = typer.Argument(..., help="Domain to show")) -> None:
    """Show the assignment for one domain (404 if none)."""
    a = hub_client.get_assignment(domain)
    if a is None:
        console.print(f"[red]No assignment for {domain}[/red]")
        raise typer.Exit(1)
    console.print(a)


@app.command()
def remove(
    domain: str = typer.Argument(..., help="Domain to remove from the registry"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
) -> None:
    """Remove the assignment for a domain (does not touch agent-synced data)."""
    if not yes:
        confirm = typer.confirm(f"Remove assignment for {domain}?", default=False)
        if not confirm:
            raise typer.Abort()
    with audit("fleet.remove", target=domain):
        hub_client.delete_assignment(domain)
        console.print(f"[green]✓[/green] Removed assignment for {domain}")


@app.command()
def backfill(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change"),
) -> None:
    """Create default assignments for domains that don't have one yet.

    For each domain visible in the agent-synced ``domains`` table, if there is
    no matching ``domain_assignments`` entry, create one with
    ``primary_vps_id`` set to the unique VPS hosting it (and empty standbys).

    Domains hosted on multiple VPS are *skipped* — they need an explicit
    `vsa fleet assign --primary X` decision.
    """
    domains = hub_client.list_domains()
    existing = {a["domain"]: a for a in hub_client.list_assignments()}

    # Group domain rows by domain → set of vps_ids
    by_domain: dict[str, set[str]] = defaultdict(set)
    for d in domains:
        by_domain[d["domain"]].add(d["vps_id"])

    created = 0
    skipped_existing = 0
    skipped_multi = 0
    for domain, vps_ids in sorted(by_domain.items()):
        if domain in existing:
            skipped_existing += 1
            continue
        if len(vps_ids) > 1:
            skipped_multi += 1
            console.print(
                f"[yellow]skip[/yellow] {domain}: hosted on {sorted(vps_ids)} — "
                "use `vsa fleet assign` to pick a primary"
            )
            continue
        primary = next(iter(vps_ids))
        if dry_run:
            console.print(f"[dim](dry-run)[/dim] would create {domain} → primary={primary}")
        else:
            hub_client.upsert_assignment(
                domain,
                primary_vps_id=primary,
                standby_vps_ids=[],
                notes="auto-backfilled — no explicit standby chosen",
            )
            console.print(f"[green]+[/green] {domain} → primary={primary}")
        created += 1

    console.print(
        f"\n[bold]Summary:[/bold] {created} created, "
        f"{skipped_existing} already existed, {skipped_multi} multi-host (manual)"
    )


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def exec(
    ctx: typer.Context,
    vps: str = typer.Option(..., "--vps", help="Target vps_id (e.g. vps-03)"),
    timeout: int = typer.Option(
        120, "--timeout", help="Seconds to wait for the command to complete"
    ),
    poll_interval: float = typer.Option(
        2.0, "--poll-interval", help="Seconds between status polls"
    ),
) -> None:
    """Run a `vsa <args>` command on a remote VPS via the hub command queue.

    Usage:

        vsa fleet exec --vps vps-03 -- cert health
        vsa fleet exec --vps vps-02 -- vhost sync

    The args after ``--`` are the argv passed to the remote `vsa`. Output
    streams back once the agent has executed it (next agent tick = up to
    ~30s of latency).
    """
    argv = list(ctx.args)
    if not argv:
        console.print(
            "[red]Need a command after `--`, e.g. "
            "`vsa fleet exec --vps vps-03 -- cert health`[/red]"
        )
        raise typer.Exit(2)

    requested_by = getpass.getuser()
    with audit(
        "fleet.exec",
        target=vps,
        params={"argv": argv, "timeout": timeout},
    ):
        cmd = hub_client.enqueue_command(
            vps_id=vps,
            argv=argv,
            timeout_seconds=timeout,
            requested_by=requested_by,
        )
        console.print(
            f"[dim]queued #{cmd['id']} on {vps}: vsa {' '.join(argv)}[/dim]"
        )

        deadline = time.time() + timeout + 30  # grace for transit
        while time.time() < deadline:
            cmd = hub_client.get_command(cmd["id"])
            if cmd["status"] == "completed":
                if cmd["stdout"]:
                    console.print(cmd["stdout"], end="")
                if cmd["stderr"]:
                    console.print(f"[red]{cmd['stderr']}[/red]", end="")
                if cmd["exit_code"] != 0:
                    raise typer.Exit(cmd["exit_code"] or 1)
                return
            time.sleep(poll_interval)

        console.print(
            f"[red]Timed out waiting for command #{cmd['id']} on {vps} "
            f"(status={cmd['status']})[/red]"
        )
        raise typer.Exit(124)


# Surface a clean error if HUB_URL/AUTH are missing — the underlying
# HubClientError already has a useful message; let typer print it.
@app.callback()
def _check_hub_config(ctx: typer.Context) -> None:
    """Verify hub config is set before any subcommand runs (except --help)."""
    if ctx.invoked_subcommand is None:
        return
    from vsa.config import get_config

    cfg = get_config()
    if not cfg.hub_url:
        raise HubClientError(
            "VSA_HUB_URL is not set. `vsa fleet …` needs the dashboard API URL. "
            "Set in /etc/vsa/agent.env: "
            "VSA_HUB_URL=https://dashboard.flowbiz.ai/api  and  "
            "VSA_HUB_AUTH=admin:<pass>"
        )
