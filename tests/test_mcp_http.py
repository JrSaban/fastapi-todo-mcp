"""Tests d'intégration du serveur MCP distant : l'application HTTP, ses contrôles d'entrée et
ce qu'elle transmet à l'API pour chaque appelant."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from mcp_server.api_client import MCP_SERVICE_KEY_HEADER, TodoApiClient
from mcp_server.config import Settings
from mcp_server.http_app import build_http_app
from mcp_server.server import build_mcp_server
from src.core.config import settings as api_settings
from src.core.security import generate_api_token, hash_token
from src.main import app as api_app
from src.modules.api_tokens.models import ApiTokenUsage
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.users.models import User

SERVICE_KEY = "k" * 64
TOKEN = "tda_caller"
AGENT_IP = "203.0.113.7"
# What every MCP client sends on a streamable HTTP request. Pinned to a version whose
# `tools/call` is a plain JSON-RPC body: the latest one requires an envelope in `_meta` that a
# hand-written request would have to rebuild.
PROTOCOL_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}

Handler = Callable[[httpx.Request], Any]


def _settings(**overrides: Any) -> Settings:
    return Settings(
        MCP_TRANSPORT="http",
        MCP_SERVICE_KEY=SERVICE_KEY,
        TODO_API_URL="http://testserver/api/v1",
        MCP_ALLOWED_HOSTS=["testserver"],
        **overrides,
    )


@asynccontextmanager
async def _remote_server(
    api_transport: httpx.AsyncBaseTransport, **overrides: Any
) -> AsyncGenerator[AsyncClient]:
    """An HTTP client on the remote MCP server, whose API calls go to `api_transport`.

    The app's lifespan runs the SDK's session manager, which httpx's ASGITransport doesn't
    start by itself.
    """
    settings = _settings(**overrides)
    server = build_mcp_server(TodoApiClient(settings, transport=api_transport))
    app = build_http_app(server, settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def _fake_api(seen: list[httpx.Request], handler: Handler | None = None) -> httpx.MockTransport:
    """An API that records every request and answers 200 with an empty object."""

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler is not None:
            await handler(request)
        return httpx.Response(200, json={})

    return httpx.MockTransport(respond)


async def _call_tool(
    client: AsyncClient,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """One JSON-RPC `tools/call`: stateless, so it needs no `initialize` first."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    return await client.post("/mcp", json=body, headers={**PROTOCOL_HEADERS, **(headers or {})})


def _tool_result(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()["result"]


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- what is refused before the MCP protocol ---


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"}, {"Authorization": TOKEN}],
    ids=["no-header", "jwt", "no-scheme"],
)
async def test_a_request_without_an_api_token_gets_401(headers: dict[str, str]) -> None:
    """Checked on every request, `initialize` included, before anything reaches the API."""
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen)) as client:
        response = await _call_tool(client, "list_todos", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert seen == []


async def test_the_bearer_scheme_is_case_insensitive() -> None:
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen)) as client:
        response = await _call_tool(
            client, "list_todos", headers={"Authorization": "bearer " + TOKEN}
        )

    assert not _tool_result(response)["isError"]
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_an_unknown_host_gets_421() -> None:
    """DNS rebinding: a page the user visits can't reach a server it was not meant for."""
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen)) as client:
        response = await _call_tool(
            client, "list_todos", headers={**_bearer(), "Host": "evil.example"}
        )

    assert response.status_code == 421
    assert seen == []


# --- what reaches the API ---


async def test_the_api_gets_the_callers_token_and_the_service_key() -> None:
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen)) as client:
        await _call_tool(client, "list_todos", headers=_bearer())

    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers[MCP_SERVICE_KEY_HEADER] == SERVICE_KEY


async def test_behind_a_trusted_proxy_the_api_gets_the_agents_ip() -> None:
    """The MCP server is the API's trusted proxy: it must forward the agent's IP, not its
    own, or every agent would share one rate-limit counter."""
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen), MCP_TRUSTED_PROXIES=["127.0.0.1"]) as client:
        await _call_tool(client, "list_todos", headers={**_bearer(), "X-Forwarded-For": AGENT_IP})

    assert seen[0].headers["X-Forwarded-For"] == AGENT_IP


async def test_without_a_trusted_proxy_a_forwarded_ip_is_ignored() -> None:
    """Anyone can write X-Forwarded-For: from an untrusted peer, the peer's own IP is sent."""
    seen: list[httpx.Request] = []
    async with _remote_server(_fake_api(seen)) as client:
        await _call_tool(client, "list_todos", headers={**_bearer(), "X-Forwarded-For": AGENT_IP})

    assert seen[0].headers["X-Forwarded-For"] == "127.0.0.1"


async def test_concurrent_callers_each_send_their_own_token() -> None:
    """Two users' calls interleaved inside the server: each API request must still carry
    the token of the MCP request that caused it, never the other one's."""
    seen: list[httpx.Request] = []
    first_arrived = asyncio.Event()

    async def hold_the_first_request(request: httpx.Request) -> None:
        # The first API request waits until the second caller's request has started, so
        # both tool calls are in flight together.
        if not first_arrived.is_set():
            first_arrived.set()
            await asyncio.sleep(0.05)

    tokens = {1: "tda_alice", 2: "tda_bob"}
    async with _remote_server(_fake_api(seen, hold_the_first_request)) as client:
        await asyncio.gather(
            *(
                _call_tool(client, "get_todo", {"todo_id": todo_id}, headers=_bearer(token))
                for todo_id, token in tokens.items()
            )
        )

    assert len(seen) == 2
    for request in seen:
        todo_id = int(request.url.path.rsplit("/", 1)[-1])
        assert request.headers["Authorization"] == f"Bearer {tokens[todo_id]}"


# --- through the real API ---


async def _committed_token(session: AsyncSession, owner_id: int, usage: ApiTokenUsage) -> str:
    """Committed, like everything a token request must see (see CLAUDE.md, Tests)."""
    plain = generate_api_token()
    await ApiTokenRepository(session).create(
        owner_id=owner_id,
        name="Remote agent",
        usage=usage,
        token_hash=hash_token(plain),
        expires_at=None,
    )
    await session.commit()
    return plain


@pytest.fixture
def api_service_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API configured with the same key as the remote server."""
    monkeypatch.setattr(api_settings, "MCP_SERVICE_KEY", SecretStr(SERVICE_KEY))


@pytest.mark.usefixtures("client", "api_service_key")
async def test_an_mcp_token_works_through_the_remote_server(
    db_session: AsyncSession, current_user: User
) -> None:
    """The whole chain: agent → remote MCP server (+ service key) → API, which accepts an
    `mcp` token only that way. `client` is requested for its overrides."""
    plain = await _committed_token(db_session, current_user.id, ApiTokenUsage.MCP)

    async with _remote_server(httpx.ASGITransport(app=api_app)) as client:
        result = _tool_result(await _call_tool(client, "list_todos", headers=_bearer(plain)))

    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["total"] == 0


@pytest.mark.usefixtures("client", "api_service_key")
async def test_an_api_token_is_refused_through_the_remote_server(
    db_session: AsyncSession, current_user: User
) -> None:
    """The remote server only relays tokens issued for it: an `api` token reaches the agent
    as the API's 401, with no hint that the token exists."""
    plain = await _committed_token(db_session, current_user.id, ApiTokenUsage.API)

    async with _remote_server(httpx.ASGITransport(app=api_app)) as client:
        result = _tool_result(await _call_tool(client, "list_todos", headers=_bearer(plain)))

    assert result["isError"]
    assert "API error 401: Token invalide ou expiré." in result["content"][0]["text"]
