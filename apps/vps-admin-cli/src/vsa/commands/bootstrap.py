"""VPS bootstrap command."""

from __future__ import annotations

import subprocess

import typer
from rich.console import Console

from vsa.audit import audit
from vsa.config import get_config

console = Console()


def _run(cmd: list[str], *, sudo: bool = False) -> None:
    """Run a command, optionally with sudo."""
    if sudo:
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True)


def bootstrap() -> None:
    """Initialize a fresh VPS with Docker, Compose, UFW, and base directories."""
    cfg = get_config()

    with audit("bootstrap", target=cfg.vps_id):
        console.print("[bold][1/5][/bold] Installing prerequisites")
        _run(
            ["apt-get", "update", "-y"],
            sudo=True,
        )
        _run(
            [
                "apt-get", "install", "-y",
                "ca-certificates", "curl", "gnupg", "lsb-release",
                "ufw", "fail2ban", "jq", "wget",
            ],
            sudo=True,
        )

        console.print("[bold][2/5][/bold] Installing Docker Engine")
        result = subprocess.run(["command", "-v", "docker"], capture_output=True, check=False)
        if result.returncode != 0:
            subprocess.run(
                ["sh", "-c", "curl -fsSL https://get.docker.com | sh"],
                check=True,
            )

        console.print("[bold][3/5][/bold] Configuring UFW firewall")
        for port in ["22/tcp", "80/tcp", "443/tcp"]:
            _run(["ufw", "allow", port], sudo=True)
        _run(["ufw", "--force", "enable"], sudo=True)

        console.print("[bold][4/5][/bold] Creating Docker network")
        from vsa.services import network
        network.ensure_external_network()

        console.print("[bold][5/5][/bold] Creating base directories")
        for d in [
            cfg.srv_base / "reverse-proxy" / "letsencrypt",
            cfg.srv_base / "reverse-proxy" / "certbot-www",
            cfg.srv_base / "reverse-proxy" / "logs",
            cfg.srv_base / "reverse-proxy" / "nginx" / "conf.d",
            cfg.srv_base / "reverse-proxy" / "nginx" / "snippets",
            cfg.srv_base / "reverse-proxy" / "nginx" / "auth",
            cfg.log_dir,
            cfg.audit_db_path.parent,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        console.print("\n[green bold]Bootstrap complete![/green bold]")
        console.print("You may need to log out/in for docker group to take effect.")
