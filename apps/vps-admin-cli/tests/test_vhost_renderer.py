"""Tests for Jinja2 vhost renderer."""

from __future__ import annotations

from pathlib import Path

from vsa_common import SiteConfig
from vsa.services.vhost_renderer import render_http_vhost, render_https_vhost, write_vhost


class TestVhostRenderer:
    def test_http_vhost_basic(self):
        site = SiteConfig(domain="example.com", container="web-1")
        config = render_http_vhost(site)
        assert "listen 80;" in config
        assert "server_name example.com www.example.com;" in config
        assert "acme-challenge" in config
        assert "301 https://$host$request_uri" in config

    def test_http_vhost_no_www(self):
        site = SiteConfig(domain="api.example.com", container="api-1", include_www=False)
        config = render_http_vhost(site)
        assert "server_name api.example.com;" in config
        assert "www.api.example.com" not in config

    def test_https_vhost_basic(self):
        site = SiteConfig(domain="example.com", container="web-1", port=3000)
        config = render_https_vhost(site)

        # HTTP block
        assert "listen 80;" in config
        assert "301 https://$host$request_uri" in config

        # HTTPS block
        assert "listen 443 ssl;" in config
        assert "http2 on;" in config
        assert "ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;" in config
        assert "ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;" in config
        assert "security_headers.conf" in config
        assert "set $upstream web-1:3000;" in config
        assert "proxy_pass http://$upstream;" in config
        assert "/healthz" in config

    def test_https_vhost_no_www(self):
        site = SiteConfig(domain="api.example.com", container="api-1", include_www=False)
        config = render_https_vhost(site)
        assert "www.api.example.com" not in config

    def test_https_vhost_with_auth(self):
        site = SiteConfig(
            domain="staging.example.com",
            container="staging-1",
            auth_basic=True,
        )
        config = render_https_vhost(site)
        assert 'auth_basic "Restricted Access";' in config
        assert "auth_basic_user_file /etc/nginx/auth/staging.example.com.htpasswd;" in config

    def test_https_vhost_without_auth(self):
        site = SiteConfig(domain="public.example.com", container="pub-1")
        config = render_https_vhost(site)
        assert "auth_basic" not in config

    def test_https_vhost_custom_timeouts(self):
        site = SiteConfig(
            domain="slow.example.com",
            container="slow-1",
            client_max_body_size="100m",
            proxy_read_timeout="300s",
            proxy_send_timeout="300s",
        )
        config = render_https_vhost(site)
        assert "client_max_body_size 100m;" in config
        assert "proxy_read_timeout 300s;" in config

    def test_https_vhost_extra_directives(self):
        site = SiteConfig(
            domain="ws.example.com",
            container="ws-1",
            extra_location_directives=[
                'proxy_set_header Upgrade $http_upgrade;',
                'proxy_set_header Connection "upgrade";',
            ],
        )
        config = render_https_vhost(site)
        assert 'proxy_set_header Upgrade $http_upgrade;' in config
        assert 'proxy_set_header Connection "upgrade";' in config

    def test_write_vhost_creates_file(self, tmp_path: Path):
        path = tmp_path / "conf.d" / "test.com.conf"
        write_vhost(path, "server { listen 80; }")
        assert path.exists()
        assert path.read_text() == "server { listen 80; }"

    def test_special_chars_in_domain(self):
        """Jinja2 templates handle special chars correctly (unlike sed)."""
        site = SiteConfig(domain="my-site.example.com", container="my-site-web-1", port=8080)
        config = render_https_vhost(site)
        assert "server_name my-site.example.com" in config
        assert "set $upstream my-site-web-1:8080;" in config
