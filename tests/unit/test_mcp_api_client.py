"""Tests unitaires du client HTTP et de la configuration du serveur MCP."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_server.api_client import MCP_SERVICE_KEY_HEADER, TodoApiClient
from mcp_server.config import Settings

TOKEN = "tda_test-token"
SERVICE_KEY = "k" * 32


def _client(handler) -> TodoApiClient:
    """A client whose requests are answered by `handler` instead of the network."""
    settings = Settings(TODO_API_URL="http://api.test/api/v1", TODO_API_TOKEN=TOKEN)
    return TodoApiClient(settings, transport=httpx.MockTransport(handler))


def _http_settings(**overrides: str) -> Settings:
    return Settings(MCP_TRANSPORT="http", MCP_SERVICE_KEY=SERVICE_KEY, **overrides)


def _http_client(handler) -> TodoApiClient:
    """The client of a remote server: a service key, and no token of its own."""
    settings = _http_settings(TODO_API_URL="http://api.test/api/v1")
    return TodoApiClient(settings, transport=httpx.MockTransport(handler))


def _recording(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    return handler


# --- Settings ---


def test_settings_require_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TODO_API_TOKEN", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_settings_refuse_something_that_is_not_an_api_token() -> None:
    """A JWT pasted by mistake stops the server at startup, not with a 401 per tool."""
    with pytest.raises(ValidationError):
        Settings(TODO_API_TOKEN="eyJhbGciOiJIUzI1NiJ9.payload.signature")


def test_settings_never_show_the_token() -> None:
    settings = Settings(TODO_API_TOKEN=TOKEN)

    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings.TODO_API_TOKEN)


def test_settings_refuse_an_empty_token() -> None:
    """Empty is not missing: it would pass the stdio check and fail every call with a 401."""
    with pytest.raises(ValidationError):
        Settings(TODO_API_TOKEN="")


def test_stdio_settings_refuse_the_service_key() -> None:
    """A local install never holds the key: it would make a stolen `mcp` token usable."""
    with pytest.raises(ValidationError, match="MCP_SERVICE_KEY"):
        Settings(TODO_API_TOKEN=TOKEN, MCP_SERVICE_KEY=SERVICE_KEY)


@pytest.mark.parametrize(
    "key", [None, "k" * 31, "change-me" + "k" * 32], ids=["missing", "too-short", "placeholder"]
)
def test_http_settings_require_a_real_service_key(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    monkeypatch.delenv("MCP_SERVICE_KEY", raising=False)

    with pytest.raises(ValidationError, match="MCP_SERVICE_KEY"):
        Settings(MCP_TRANSPORT="http", MCP_SERVICE_KEY=key)


def test_http_settings_refuse_a_token_of_their_own() -> None:
    """A remote server holding one user's token could act as that user for everyone."""
    with pytest.raises(ValidationError, match="TODO_API_TOKEN"):
        _http_settings(TODO_API_TOKEN=TOKEN)


def test_http_settings_need_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TODO_API_TOKEN", raising=False)

    assert _http_settings().TODO_API_TOKEN is None


def test_settings_refuse_an_unknown_transport() -> None:
    with pytest.raises(ValidationError, match="MCP_TRANSPORT"):
        Settings(MCP_TRANSPORT="sse", TODO_API_TOKEN=TOKEN)  # pyrefly: ignore[bad-argument-type]


# --- requests ---


async def test_every_request_carries_the_token_and_the_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 1})

    await _client(handler).request("POST", "/todos", json={}, idempotency_key="k-1")

    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers["Idempotency-Key"] == "k-1"
    assert str(seen[0].url) == "http://api.test/api/v1/todos"


async def test_a_stdio_client_sends_no_service_key_nor_forwarded_ip() -> None:
    seen: list[httpx.Request] = []

    await _client(_recording(seen)).request("GET", "/todos")

    assert MCP_SERVICE_KEY_HEADER not in seen[0].headers
    assert "X-Forwarded-For" not in seen[0].headers


async def test_a_per_request_token_wins_over_the_configured_one() -> None:
    seen: list[httpx.Request] = []

    await _client(_recording(seen)).request("GET", "/todos", token="tda_someone")

    assert seen[0].headers["Authorization"] == "Bearer tda_someone"


async def test_a_remote_client_sends_the_callers_token_ip_and_its_service_key() -> None:
    seen: list[httpx.Request] = []

    await _http_client(_recording(seen)).request(
        "GET", "/todos", token="tda_caller", client_ip="203.0.113.7"
    )

    assert seen[0].headers["Authorization"] == "Bearer tda_caller"
    assert seen[0].headers["X-Forwarded-For"] == "203.0.113.7"
    assert seen[0].headers[MCP_SERVICE_KEY_HEADER] == SERVICE_KEY


async def test_a_remote_client_without_a_caller_token_sends_nothing() -> None:
    """No fallback token on a remote server: a forgotten token is an error, never somebody
    else's identity."""
    seen: list[httpx.Request] = []

    with pytest.raises(ToolError, match="No API token"):
        await _http_client(_recording(seen)).request("GET", "/todos")

    assert seen == []


async def test_errors_never_carry_the_token_or_the_service_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Token invalide ou expiré."})

    with pytest.raises(ToolError) as exc_info:
        await _http_client(handler).request("GET", "/todos", token="tda_caller")

    assert "tda_caller" not in str(exc_info.value)
    assert SERVICE_KEY not in str(exc_info.value)


async def test_no_content_returns_none() -> None:
    result = await _client(lambda request: httpx.Response(204)).request("DELETE", "/x")

    assert result is None


# --- errors as the agent sees them ---


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(409, json={"detail": "Une catégorie avec le nom Sport existe déjà."}),
            "API error 409: Une catégorie avec le nom Sport existe déjà.",
        ),
        (
            httpx.Response(
                422,
                json={
                    "detail": [
                        {"loc": ["body", "title"], "msg": "String too short"},
                        {"loc": ["body", "category_ids", 0], "msg": "Input should be an integer"},
                    ]
                },
            ),
            "API error 422: body.title: String too short; "
            "body.category_ids.0: Input should be an integer",
        ),
        (httpx.Response(500, text="Internal Server Error"), "API error 500: Internal Server Error"),
        (httpx.Response(502, json={"error": "bad gateway"}), 'API error 502: {"error":'),
    ],
    ids=["plain-detail", "validation-list", "not-json", "json-without-detail"],
)
async def test_api_errors_become_readable_tool_errors(
    response: httpx.Response, expected: str
) -> None:
    with pytest.raises(ToolError) as exc_info:
        await _client(lambda request: response).request("GET", "/todos")

    assert str(exc_info.value).startswith(expected)


async def test_unreachable_api_is_a_tool_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ToolError, match=r"API unreachable \(ConnectError\)"):
        await _client(handler).request("GET", "/todos")
