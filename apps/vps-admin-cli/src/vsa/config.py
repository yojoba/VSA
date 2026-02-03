"""CLI configuration — singleton VsaConfig resolved at startup."""

from __future__ import annotations

from functools import lru_cache

from vsa_common import VsaConfig


@lru_cache(maxsize=1)
def get_config() -> VsaConfig:
    """Return the global VsaConfig (resolved once, cached)."""
    return VsaConfig()
