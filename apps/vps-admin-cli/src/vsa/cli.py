"""Root Typer application for the VSA CLI."""

from __future__ import annotations

import typer

from vsa.commands import agent, auth, bootstrap, cert, site, stack, vhost, vps

app = typer.Typer(
    name="vsa",
    help="FlowBiz VPS Admin Suite — manage multi-tenant hosting infrastructure.",
    no_args_is_help=True,
)

app.add_typer(site.app, name="site", help="Provision / unprovision sites behind the reverse proxy.")
app.add_typer(cert.app, name="cert", help="SSL certificate management.")
app.add_typer(auth.app, name="auth", help="HTTP Basic Auth management.")
app.add_typer(stack.app, name="stack", help="Docker Compose stack lifecycle.")
app.add_typer(vhost.app, name="vhost", help="NGINX vhost management.")
app.add_typer(agent.app, name="agent", help="Multi-VPS agent management.")
app.add_typer(vps.app, name="vps", help="VPS node fleet management.")
app.command(name="bootstrap")(bootstrap.bootstrap)

if __name__ == "__main__":
    app()
