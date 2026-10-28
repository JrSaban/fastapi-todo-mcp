"""Tests unitaires du point d'entrée du serveur MCP : le mode choisi par `MCP_TRANSPORT`."""

from unittest.mock import MagicMock, patch

import pytest

from mcp_server.__main__ import main

SERVICE_KEY = "k" * 32


@patch("mcp_server.__main__.uvicorn.run")
def test_http_transport_runs_uvicorn_without_its_proxy_headers(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uvicorn would trust 127.0.0.1's X-Forwarded-For by default, next to
    MCP_TRUSTED_PROXIES."""
    monkeypatch.delenv("TODO_API_TOKEN", raising=False)
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_SERVICE_KEY", SERVICE_KEY)
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9001")

    main()

    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs == {"host": "0.0.0.0", "port": 9001, "proxy_headers": False}


@patch("mcp_server.__main__.uvicorn.run")
@patch("mcp_server.__main__.build_mcp_server")
def test_stdio_transport_serves_over_stdio(
    mock_build: MagicMock, mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_SERVICE_KEY", raising=False)
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.setenv("TODO_API_TOKEN", "tda_test-token")

    main()

    mock_build.return_value.run.assert_called_once_with("stdio")
    mock_run.assert_not_called()
