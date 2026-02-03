"""Jinja2-based NGINX vhost config renderer."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from vsa_common import SiteConfig

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _get_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape([]),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_https_vhost(site: SiteConfig) -> str:
    """Render a full HTTPS vhost config (HTTP redirect + SSL block)."""
    env = _get_env()
    template = env.get_template("vhost_https.conf.j2")
    return template.render(site=site)


def render_http_vhost(site: SiteConfig) -> str:
    """Render an HTTP-only vhost for ACME challenge (pre-cert issuance)."""
    env = _get_env()
    template = env.get_template("vhost_http.conf.j2")
    return template.render(site=site)


def write_vhost(path: Path, content: str) -> None:
    """Write vhost config to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
