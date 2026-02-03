"""Custom exceptions for the VSA CLI."""

from __future__ import annotations


class VsaError(Exception):
    """Base exception for all VSA operations."""

    def __init__(self, message: str, *, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class NginxConfigError(VsaError):
    """NGINX configuration validation failed."""


class CertbotError(VsaError):
    """Certbot operation failed."""


class DockerError(VsaError):
    """Docker/Compose operation failed."""


class VhostNotFoundError(VsaError):
    """Requested vhost file does not exist."""


class ContainerNotFoundError(VsaError):
    """No container found matching criteria."""
