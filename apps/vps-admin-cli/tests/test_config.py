"""Tests for VsaConfig."""

from __future__ import annotations

from pathlib import Path

from vsa_common import VsaConfig


class TestVsaConfig:
    def test_default_paths(self, tmp_config: VsaConfig):
        assert tmp_config.vps_id == "test-vps"
        assert tmp_config.repo_vhost_dir == tmp_config.vsa_root / "stacks/reverse-proxy/nginx/conf.d"
        assert tmp_config.repo_snippets_dir == tmp_config.vsa_root / "stacks/reverse-proxy/nginx/snippets"
        assert tmp_config.repo_auth_dir == tmp_config.vsa_root / "stacks/reverse-proxy/nginx/auth"

    def test_mount_paths(self, tmp_config: VsaConfig):
        assert tmp_config.mount_vhost_dir == tmp_config.srv_base / "reverse-proxy/nginx/conf.d"
        assert tmp_config.mount_auth_dir == tmp_config.srv_base / "reverse-proxy/nginx/auth"

    def test_reverse_proxy_compose(self, tmp_config: VsaConfig):
        assert tmp_config.reverse_proxy_compose == tmp_config.vsa_root / "stacks/reverse-proxy/compose.yml"

    def test_from_env(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("VSA_ROOT", str(tmp_path))
        monkeypatch.setenv("VSA_VPS_ID", "vps-99")
        monkeypatch.setenv("VSA_CERTBOT_EMAIL", "test@example.com")
        cfg = VsaConfig()
        assert cfg.vsa_root == tmp_path
        assert cfg.vps_id == "vps-99"
        assert cfg.certbot_email == "test@example.com"
