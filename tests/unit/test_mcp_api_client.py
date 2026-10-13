"""Tests unitaires du client HTTP et de la configuration du serveur MCP."""

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_server.api_client import TodoApiClient
from mcp_server.config import Settings

TOKEN = "tda_test-token"


def _client(handler) -> TodoApiClient:
    """A client whose requests are answered by `handler` instead of the network."""
    settings = Settings(TODO_API_URL="http://api.test/api/v1", TODO_API_TOKEN=TOKEN)
    return TodoApiClient(settings, transport=httpx.MockTransport(handler))


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
