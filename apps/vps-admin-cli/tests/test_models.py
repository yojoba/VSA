"""Tests for shared Pydantic models."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from vsa_common import AuditEvent, SiteConfig


class TestAuditEvent:
    def test_defaults(self):
        event = AuditEvent(action="test.action", target="example.com")
        assert event.action == "test.action"
        assert event.target == "example.com"
        assert event.result == "success"
        assert event.vps_id == "vps-01"
        assert event.error is None
        assert isinstance(event.timestamp, datetime)

    def test_to_jsonl(self):
        event = AuditEvent(
            action="site.provision",
            target="example.com",
            actor="testuser",
            params={"port": 3000},
        )
        line = event.to_jsonl()
        data = json.loads(line)
        assert data["action"] == "site.provision"
        assert data["target"] == "example.com"
        assert data["params"]["port"] == 3000
        assert data["result"] == "success"

    def test_failure_event(self):
        event = AuditEvent(
            action="cert.issue",
            target="fail.com",
            result="failure",
            error="certbot failed",
            duration_ms=1234,
        )
        assert event.result == "failure"
        assert event.error == "certbot failed"
        assert event.duration_ms == 1234


class TestSiteConfig:
    def test_defaults(self):
        site = SiteConfig(domain="example.com", container="web-1")
        assert site.domain == "example.com"
        assert site.container == "web-1"
        assert site.port == 3000
        assert site.include_www is True
        assert site.ssl is True
        assert site.client_max_body_size == "20m"
        assert site.auth_basic is False

    def test_no_www(self):
        site = SiteConfig(domain="api.example.com", container="api-1", include_www=False)
        assert site.include_www is False

    def test_with_auth(self):
        site = SiteConfig(
            domain="staging.example.com",
            container="staging-1",
            auth_basic=True,
            auth_user="admin",
        )
        assert site.auth_basic is True
        assert site.auth_user == "admin"
