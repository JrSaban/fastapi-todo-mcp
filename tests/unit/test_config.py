"""Tests unitaires de la validation des réglages."""

import pytest
from pydantic import ValidationError

from src.core.config import Settings


def _with_service_key(key: str | None) -> Settings:
    """Settings that ignore the developer's `.env`, so only the given key is checked."""
    return Settings(_env_file=None, MCP_SERVICE_KEY=key)  # pyrefly: ignore[unexpected-keyword]


def test_mcp_service_key_is_optional() -> None:
    """No key means no remote MCP server, not a startup failure."""
    assert _with_service_key(None).MCP_SERVICE_KEY is None


def test_a_long_enough_mcp_service_key_is_accepted_and_hidden() -> None:
    key = "k" * 32

    configured = _with_service_key(key).MCP_SERVICE_KEY

    assert configured is not None
    assert configured.get_secret_value() == key
    assert key not in repr(configured)


@pytest.mark.parametrize("key", ["", "k" * 31, "change-me-with-openssl-rand-hex-32"])
def test_a_weak_mcp_service_key_refuses_to_start(key: str) -> None:
    """A short key, or the placeholder someone forgot to replace, would let anyone pass for
    the remote MCP server."""
    with pytest.raises(ValidationError, match="MCP_SERVICE_KEY"):
        _with_service_key(key)
