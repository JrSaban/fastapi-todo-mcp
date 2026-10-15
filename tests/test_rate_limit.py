"""Tests d'intégration du middleware de rate limiting général."""

from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from httpx import AsyncClient
from redis.exceptions import RedisError

from src.core.config import settings
from src.core.security import create_access_token

IP_KEY = "rate_limit:general:ip:127.0.0.1"
# Any route counted by the middleware. Without a valid session it answers 401, so a request
# that gets through is checked with `!= 429`: these tests are about the middleware, not the route.
COUNTED_PATH = "/api/v1/todos"


async def test_request_under_the_limit_passes(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The request that brings the counter exactly to GENERAL_RATE_LIMIT_MAX_REQUESTS
    still passes — the limit is inclusive."""
    await redis_client.set(IP_KEY, str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS - 1))

    response = await client.get(COUNTED_PATH)
    assert response.status_code != 429


async def test_request_over_the_limit_returns_429(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """Once GENERAL_RATE_LIMIT_MAX_REQUESTS requests have been made, the next one → 429."""
    await redis_client.set(IP_KEY, str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS))

    response = await client.get(COUNTED_PATH)
    assert response.status_code == 429


async def test_blocked_request_carries_retry_after(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """A 429 tells the client how long to wait: Retry-After is the counter's remaining
    TTL, in seconds, so it can never exceed the window."""
    window_seconds = 60 * settings.GENERAL_RATE_LIMIT_WINDOW_MINUTES
    await redis_client.set(IP_KEY, str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS), ex=window_seconds)

    response = await client.get(COUNTED_PATH)
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= window_seconds


async def test_blocked_request_still_carries_request_id(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The rate limit middleware sits inside log_requests, so a 429 short-circuit
    still goes through it and gets an X-Request-ID."""
    await redis_client.set(IP_KEY, str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS))

    response = await client.get(COUNTED_PATH)
    assert response.status_code == 429
    assert "X-Request-ID" in response.headers


async def test_first_request_sets_the_window_ttl(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The first request of a window creates the counter with a TTL of
    GENERAL_RATE_LIMIT_WINDOW_MINUTES."""
    await client.get(COUNTED_PATH)

    assert await redis_client.get(IP_KEY) == "1"
    ttl = await redis_client.ttl(IP_KEY)
    assert 0 < ttl <= 60 * settings.GENERAL_RATE_LIMIT_WINDOW_MINUTES


async def test_valid_token_is_counted_per_user(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """A valid access token is counted under the user's id, not the IP."""
    token = create_access_token({"sub": "42"})

    await client.get(COUNTED_PATH, headers={"Authorization": f"Bearer {token}"})

    assert await redis_client.get("rate_limit:general:user:42") == "1"
    assert await redis_client.get(IP_KEY) is None


@pytest.mark.parametrize("scheme", ["bearer", "BEARER"])
async def test_scheme_case_does_not_matter(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis, scheme: str
) -> None:
    """The routes accept the scheme in any case (HTTPBearer): the middleware must read the
    same user, or switching case would hand the user a second quota counted by IP."""
    token = create_access_token({"sub": "42"})

    await client.get(COUNTED_PATH, headers={"Authorization": f"{scheme} {token}"})

    assert await redis_client.get("rate_limit:general:user:42") == "1"
    assert await redis_client.get(IP_KEY) is None


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Basic abc"])
async def test_no_bearer_token_is_counted_by_ip(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis, header: str
) -> None:
    """No token after the scheme, or another scheme: no user, the request counts by IP."""
    await client.get(COUNTED_PATH, headers={"Authorization": header})

    assert await redis_client.get(IP_KEY) == "1"


async def test_exhausted_user_does_not_block_same_ip(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """A user who exhausted their own counter doesn't block anonymous requests
    coming from the same IP — the two counters are independent."""
    await redis_client.set(
        "rate_limit:general:user:42", str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS)
    )
    token = create_access_token({"sub": "42"})

    blocked = await client.get(COUNTED_PATH, headers={"Authorization": f"Bearer {token}"})
    assert blocked.status_code == 429

    anonymous = await client.get(COUNTED_PATH)
    assert anonymous.status_code != 429


async def test_invalid_token_falls_back_to_ip(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """An invalid or expired token never makes the middleware fail: the request
    is counted under the IP and goes on."""
    response = await client.get(COUNTED_PATH, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code != 429
    assert await redis_client.get(IP_KEY) == "1"


async def test_non_bearer_header_falls_back_to_ip(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """An Authorization header with another scheme is ignored: counted under the IP."""
    response = await client.get(COUNTED_PATH, headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert response.status_code != 429
    assert await redis_client.get(IP_KEY) == "1"


async def test_health_is_not_counted(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """/health bypasses the middleware: it answers even when the IP's counter is
    exhausted, and doesn't increment it."""
    await redis_client.set(IP_KEY, str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS))

    response = await client.get("/health")

    assert response.status_code == 200
    assert await redis_client.get(IP_KEY) == str(settings.GENERAL_RATE_LIMIT_MAX_REQUESTS)


async def test_redis_unavailable_lets_the_request_through(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Redis fails, the middleware doesn't count the request and lets it reach the
    route (401 here, no token) instead of turning it into a 500."""
    broken_redis = MagicMock()
    broken_redis.incr = AsyncMock(side_effect=RedisError("Redis is down"))
    monkeypatch.setattr("src.core.redis.redis_client", broken_redis)

    response = await client.get(COUNTED_PATH)

    assert response.status_code == 401
