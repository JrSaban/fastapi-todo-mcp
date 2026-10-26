"""Tests d'intégration de la lecture de l'IP client derrière un reverse proxy de confiance."""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.core.config import Settings
from src.main import app

PROXY_IP = "10.0.0.5"
CLIENT_IP = "203.0.113.7"
SPOOFED_IP = "198.51.100.66"
# Any route counted by the general limit: these tests are about the IP, not the route.
COUNTED_PATH = "/api/v1/todos"

ClientFrom = Callable[[str], AbstractAsyncContextManager[AsyncClient]]


def _general_key(ip: str) -> str:
    return f"rate_limit:general:ip:{ip}"


def _trust(monkeypatch: pytest.MonkeyPatch, trusted_proxies: list[str]) -> None:
    """Rebuild the app's middleware stack as if TRUSTED_PROXIES were `trusted_proxies`.

    The middleware reads the setting once, when the stack is built on the first request:
    patching `settings` afterwards would change nothing. Both changes are undone after the test.
    """
    proxy_middleware = next(m for m in app.user_middleware if m.cls is ProxyHeadersMiddleware)
    monkeypatch.setitem(proxy_middleware.kwargs, "trusted_hosts", trusted_proxies)
    monkeypatch.setattr(app, "middleware_stack", None)


@pytest.fixture
def client_from(client: AsyncClient) -> ClientFrom:
    """Open a client whose requests come from the given IP. `client` is requested for its
    overrides (test database, fake Redis), which apply to the whole app."""

    @asynccontextmanager
    async def open_client(peer_ip: str) -> AsyncGenerator[AsyncClient, None]:
        transport = ASGITransport(app=app, client=(peer_ip, 12345))
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http

    return open_client


def test_no_proxy_is_trusted_by_default() -> None:
    assert Settings.model_fields["TRUSTED_PROXIES"].default == []


def test_the_proxy_middleware_wraps_every_other_middleware() -> None:
    """Declared last, it runs first: the rate limits must see the IP it rewrote."""
    assert app.user_middleware[0].cls is ProxyHeadersMiddleware


async def test_without_trusted_proxy_the_header_is_ignored(
    client: AsyncClient,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no trusted proxy (the default), even a request from localhost can't choose its IP:
    unlike Uvicorn's own handling, which trusts 127.0.0.1 and is disabled for that reason. The
    empty list is set here, not read from the app: a developer's `.env` may fill it."""
    _trust(monkeypatch, [])

    await client.get(COUNTED_PATH, headers={"X-Forwarded-For": SPOOFED_IP})

    assert await redis_client.get(_general_key("127.0.0.1")) == "1"
    assert await redis_client.get(_general_key(SPOOFED_IP)) is None


async def test_a_trusted_proxy_forwards_the_client_ip(
    client_from: ClientFrom,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust(monkeypatch, [PROXY_IP])

    async with client_from(PROXY_IP) as http:
        await http.get(COUNTED_PATH, headers={"X-Forwarded-For": CLIENT_IP})

    assert await redis_client.get(_general_key(CLIENT_IP)) == "1"
    assert await redis_client.get(_general_key(PROXY_IP)) is None


async def test_a_trusted_range_forwards_the_client_ip(
    client_from: ClientFrom,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trust(monkeypatch, ["10.0.0.0/8"])

    async with client_from("10.1.2.3") as http:
        await http.get(COUNTED_PATH, headers={"X-Forwarded-For": CLIENT_IP})

    assert await redis_client.get(_general_key(CLIENT_IP)) == "1"


async def test_a_client_that_is_not_a_trusted_proxy_cannot_choose_its_ip(
    client_from: ClientFrom,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy is trusted, but this request reaches the API directly."""
    _trust(monkeypatch, [PROXY_IP])

    async with client_from(CLIENT_IP) as http:
        await http.get(COUNTED_PATH, headers={"X-Forwarded-For": SPOOFED_IP})

    assert await redis_client.get(_general_key(CLIENT_IP)) == "1"
    assert await redis_client.get(_general_key(SPOOFED_IP)) is None


async def test_an_ip_spoofed_through_the_proxy_is_not_used(
    client_from: ClientFrom,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client sends its own X-Forwarded-For and the proxy appends the real IP to it: the
    rightmost address that isn't a trusted proxy is the client's, not the leftmost."""
    _trust(monkeypatch, [PROXY_IP])

    async with client_from(PROXY_IP) as http:
        await http.get(COUNTED_PATH, headers={"X-Forwarded-For": f"{SPOOFED_IP}, {CLIENT_IP}"})

    assert await redis_client.get(_general_key(CLIENT_IP)) == "1"
    assert await redis_client.get(_general_key(SPOOFED_IP)) is None


async def test_the_login_limit_counts_the_forwarded_ip(
    client_from: ClientFrom,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The login limit reads the IP in the router, not in a middleware: it sees the rewritten
    one too."""
    _trust(monkeypatch, [PROXY_IP])

    async with client_from(PROXY_IP) as http:
        response = await http.post(
            "/api/v1/login",
            json={"email": "nobody@test.com", "password": "wrong-password"},
            headers={"X-Forwarded-For": CLIENT_IP},
        )

    assert response.status_code == 401
    assert await redis_client.get(f"rate_limit:login:ip:{CLIENT_IP}") == "1"
    assert await redis_client.get(f"rate_limit:login:ip:{PROXY_IP}") is None
