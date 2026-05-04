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


def _print_remote_result(cmd: dict, label: str = "") -> None:
    if cmd["status"] != "completed":
        console.print(
            f"[red]Timed out waiting for command #{cmd['id']} on {cmd['vps_id']} "
            f"(status={cmd['status']}){' — ' + label if label else ''}[/red]"
        )
        raise typer.Exit(124)
    if cmd["stdout"]:
        console.print(cmd["stdout"], end="")
    if cmd["stderr"]:
        console.print(f"[red]{cmd['stderr']}[/red]", end="")
    if cmd["exit_code"] != 0:
        raise typer.Exit(cmd["exit_code"] or 1)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def exec(
    ctx: typer.Context,
    vps: str = typer.Option(..., "--vps", help="Target vps_id (e.g. vps-03)"),
    timeout: int = typer.Option(
        120, "--timeout", help="Seconds to wait for the command to complete"
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

    with audit("fleet.exec", target=vps, params={"argv": argv, "timeout": timeout}):
        cmd = hub_client.exec_and_wait(
            vps_id=vps,
            argv=argv,
            timeout=timeout,
            requested_by=getpass.getuser(),
        )
        console.print(f"[dim]ran #{cmd['id']} on {vps}: vsa {' '.join(argv)}[/dim]")
        _print_remote_result(cmd)


# ---------------------------------------------------------------------------
# Convenience wrappers: thin frontends to common `fleet exec` use cases
# ---------------------------------------------------------------------------


@app.command("vhost-sync")
def vhost_sync(
    vps: str = typer.Option(..., "--vps", help="Target vps_id"),
) -> None:
    """Run `vsa vhost sync` on a remote VPS (copies repo confs into the bind-mount)."""
    with audit("fleet.vhost-sync", target=vps):
        cmd = hub_client.exec_and_wait(
            vps_id=vps,
            argv=["vhost", "sync"],
            timeout=60,
            requested_by=getpass.getuser(),
        )
        _print_remote_result(cmd)


@app.command("cert-renew")
def cert_renew(
    vps: str = typer.Option(..., "--vps", help="Target vps_id"),
) -> None:
    """Run `vsa cert renew` on a remote VPS."""
    with audit("fleet.cert-renew", target=vps):
        cmd = hub_client.exec_and_wait(
            vps_id=vps,
            argv=["cert", "renew"],
            timeout=300,
            requested_by=getpass.getuser(),
        )
        _print_remote_result(cmd)


@app.command("cert-health")
def cert_health(
    vps: str = typer.Option(
        "",
        "--vps",
        help="Target vps_id (omit + use --all for the whole fleet)",
    ),
    all_vps: bool = typer.Option(
        False, "--all", help="Run on every VPS in the registry"
    ),
) -> None:
    """Run `vsa cert health` on one VPS or the whole fleet."""
    targets: list[str]
    if all_vps:
        from vsa.services import hub_client as hc

        # Slight reach: piggy-back the agent endpoint via the user-side client
        with hc._client() as client:
            resp = client.get("/vps")
            hc._check(resp)
            targets = [n["vps_id"] for n in resp.json()]
    elif vps:
        targets = [vps]
    else:
        console.print("[red]Specify --vps X or --all[/red]")
        raise typer.Exit(2)

    failures = 0
    for t in targets:
        console.print(f"\n[bold cyan]── {t} ──[/bold cyan]")
        with audit("fleet.cert-health", target=t):
            cmd = hub_client.exec_and_wait(
                vps_id=t,
                argv=["cert", "health"],
                timeout=60,
                requested_by=getpass.getuser(),
            )
        try:
            _print_remote_result(cmd)
        except typer.Exit as exc:
            if exc.exit_code:
                failures += 1
    if failures:
        raise typer.Exit(1)


@app.command("site-provision")
def site_provision(
    domain: str = typer.Option(..., help="Domain name"),
    primary: str = typer.Option(..., "--primary", help="Primary vps_id"),
    standbys: str = typer.Option(
        "",
        "--standbys",
        help="Comma-separated standby vps_ids (each gets vhost+DNS-01 cert, no container)",
    ),
    container: str = typer.Option(..., "--container", help="Container name"),
    port: int = typer.Option(..., "--port", help="Internal container port"),
    no_www: bool = typer.Option(False, "--no-www", help="Skip www subdomain"),
) -> None:
    """Provision a domain on its primary VPS + warm standbys, then record the assignment.

    On the primary, runs the full `vsa site provision` (HTTP-01 webroot).
    On each standby, runs `vsa site provision … --standby` (DNS-01 Cloudflare,
    no container attach — the upstream lives on the primary).

    Updates the domain_assignments registry on success so the dashboard
    reflects the new layout.
    """
    standby_list = _split_csv(standbys)
    base_argv = ["site", "provision", "--domain", domain, "--container", container, "--port", str(port)]
    if no_www:
        base_argv.append("--no-www")

    with audit(
        "fleet.site-provision",
        target=domain,
        params={"primary": primary, "standbys": standby_list, "container": container, "port": port},
    ):
        # Primary first — needs to succeed before standbys are provisioned
        console.print(f"[bold cyan]── primary {primary} ──[/bold cyan]")
        cmd = hub_client.exec_and_wait(
            vps_id=primary,
            argv=base_argv,
            timeout=300,
            requested_by=getpass.getuser(),
        )
        _print_remote_result(cmd, label="primary failed; aborting standby rollout")

        for sb in standby_list:
            console.print(f"\n[bold cyan]── standby {sb} ──[/bold cyan]")
            cmd = hub_client.exec_and_wait(
                vps_id=sb,
                argv=base_argv + ["--standby"],
                timeout=300,
                requested_by=getpass.getuser(),
            )
            _print_remote_result(cmd, label=f"standby {sb} failed (other standbys + assignment will continue)")

        hub_client.upsert_assignment(
            domain,
            primary_vps_id=primary,
            standby_vps_ids=standby_list,
            notes=f"provisioned via `vsa fleet site-provision` ({container}:{port})",
        )
        console.print(
            f"\n[green bold]Done![/green bold] {domain} provisioned on "
            f"primary={primary} standbys={standby_list or 'none'}"
        )


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
