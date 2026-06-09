"""Fleet alerting commands — email alarms for cert + system problems.

Runs against the hub API (needs ``VSA_HUB_URL`` + ``VSA_HUB_AUTH``) and sends
email via SMTP. All config is env-driven (see ``/etc/vsa/alert.env``):

    VSA_ALERT_SMTP_HOST=mail.infomaniak.com
    VSA_ALERT_SMTP_PORT=587
    VSA_ALERT_SMTP_USER=alarms@lokalflash.ch
    VSA_ALERT_SMTP_PASSWORD=********
    VSA_ALERT_FROM=alarms@lokalflash.ch
    VSA_ALERT_TO=alexandre@netcool.ch,info@flowbiz.ai
    VSA_ALERT_MIN_LEVEL=warning            # info | warning | critical
    VSA_ALERT_AGENT_STALE_MINUTES=10
    VSA_ALERT_IGNORE_CONTAINERS=           # comma substrings to skip
    VSA_ALERT_PROMETHEUS_URL=http://localhost:9090  # disk metrics source
    VSA_ALERT_DISK_WARN_PERCENT=85         # warn at >= this % full
    VSA_ALERT_DISK_CRIT_PERCENT=92         # critical at >= this % full
    VSA_ALERT_DISK_MOUNTS=/|/var/lib/docker  # PromQL regex of mountpoints
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.table import Table

from vsa.audit import audit
from vsa.services import alerting

app = typer.Typer(no_args_is_help=True)
console = Console()

_LEVEL_STYLE = {"critical": "bold red", "warning": "yellow", "info": "cyan"}


def _load_cfg() -> alerting.AlertConfig:
    cfg = alerting.AlertConfig.from_env()
    errs = cfg.validate()
    if errs:
        console.print("[red]Alert config incomplete:[/red]")
        for e in errs:
            console.print(f"  • {e}")
        console.print("\nSet them in [cyan]/etc/vsa/alert.env[/cyan] (see `vsa alert --help`).")
        raise typer.Exit(2)
    return cfg


def _render_table(problems: list[alerting.Problem]) -> Table:
    table = Table(title=f"{len(problems)} active problem(s)")
    table.add_column("Level")
    table.add_column("Category")
    table.add_column("Target")
    table.add_column("VPS")
    table.add_column("Detail")
    for p in problems:
        table.add_row(
            f"[{_LEVEL_STYLE.get(p.level, 'white')}]{p.level}[/]",
            p.category, p.target, p.vps, p.detail,
        )
    return table


@app.command()
def status() -> None:
    """Show current fleet problems WITHOUT sending any email.

    Examples:

        vsa alert status
    """
    cfg = alerting.AlertConfig.from_env()  # status doesn't need SMTP creds
    problems = alerting.collect_problems(cfg)
    if not problems:
        console.print("[green]✓ No problems at or above level "
                      f"'{cfg.min_level}'.[/green]")
        return
    console.print(_render_table(problems))


@app.command()
def check(
    force: bool = typer.Option(
        False, "--force", help="Send a digest even if nothing changed since last run."
    ),
    notify_resolved: bool = typer.Option(
        True, "--notify-resolved/--no-notify-resolved",
        help="Send a recovery email when all problems clear.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute + print what would be sent, but don't email."
    ),
) -> None:
    """Check the fleet and email an alert when problems appear or change.

    Designed to run on a timer. To avoid noise it only emails when the set of
    active problems CHANGES (a new/escalated problem, or a full recovery) —
    unchanged problems don't re-alert. Use --force to always send.

    Examples:

        vsa alert check                 # the timer's default
        vsa alert check --force         # send the current state now
        vsa alert check --dry-run       # preview, no email
    """
    cfg = _load_cfg()
    now = datetime.now(timezone.utc)
    current = alerting.collect_problems(cfg, now=now)
    current_keys = {p.key for p in current}
    previous = alerting.load_state(cfg.state_path)

    new = [p for p in current if p.key not in previous]
    resolved_keys = previous - current_keys
    recovered = bool(previous) and not current

    should_email = force or bool(new) or (recovered and notify_resolved)

    console.print(
        f"[dim]{len(current)} active ({len(new)} new), "
        f"{len(resolved_keys)} resolved — "
        f"{'EMAIL' if should_email else 'no change, silent'}[/dim]"
    )
    if current:
        console.print(_render_table(current))

    if should_email:
        subject = alerting.render_subject(cfg, current, resolved=recovered)
        text, html = alerting.render_bodies(cfg, current, new, resolved_keys, now=now)
        if dry_run:
            console.print(f"\n[yellow]DRY-RUN[/yellow] would email "
                          f"{', '.join(cfg.recipients)}\nSubject: {subject}\n")
            console.print(text)
        else:
            with audit("alert.send", target=",".join(cfg.recipients),
                       active=len(current), new=len(new),
                       resolved=len(resolved_keys)):
                alerting.send_email(cfg, subject, text, html)
            console.print(f"[green]✓ Alert emailed to {', '.join(cfg.recipients)}[/green]")

    if not dry_run:
        alerting.save_state(cfg.state_path, current, now=now)

    # Non-zero exit when critical problems are active — handy for timers / CI.
    if any(p.level == "critical" for p in current):
        raise typer.Exit(1)


@app.command()
def test() -> None:
    """Send a test email to verify SMTP config + recipients.

    Examples:

        vsa alert test
    """
    cfg = _load_cfg()
    now = datetime.now(timezone.utc)
    probe = alerting.Problem(
        "info", "test", "—", "smtp-config",
        "ceci est un test d'alerte VSA — la configuration email fonctionne.",
    )
    subject = f"{cfg.subject_prefix} ✅ Test d'alerte"
    text, html = alerting.render_bodies(cfg, [probe], [probe], set(), now=now)
    with audit("alert.test", target=",".join(cfg.recipients)):
        alerting.send_email(cfg, subject, text, html)
    console.print(f"[green]✓ Test email sent to {', '.join(cfg.recipients)}[/green]")
