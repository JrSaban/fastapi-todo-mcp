"""Tests d'intégration des limites de tentatives atomiques (connexion et actions sensibles)."""

import asyncio
from typing import Any

import fakeredis
import pytest
from httpx import AsyncClient

from src.core.config import settings
from src.core.exceptions import InvalidPasswordError, UnauthorizedError
from src.core.rate_limit import release_attempt, reserve_attempt
from src.main import app
from src.modules.auth.router import get_auth_service, sensitive_action_rate_limit_key
from src.modules.users.models import User
from src.modules.users.router import get_user_service

IP_KEY = "rate_limit:login:ip:127.0.0.1"
EMAIL_KEY = "rate_limit:login:email:test@gmail.com"
PASSWORD = "secret123"
# Long enough for LoginRequest: a shorter one would be a 422, before any limit.
WRONG_PASSWORD = "wrong_password"
# More requests than the limit, sent at once: the attack the reservation closes.
PARALLEL_REQUESTS = 20


class SlowFailingService:
    """Stands in for `AuthService` and `UserService`: every login or password check fails,
    after yielding to the event loop so that parallel requests really overlap. Without the
    pause, each request would run to completion before the next one starts."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def _fail(self, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.01)
        raise self.error

    login = update_email = update_password = _fail


async def _counter(redis_client: fakeredis.FakeAsyncRedis, key: str) -> int:
    """A counter's value, a missing key counting as 0."""
    return int(await redis_client.get(key) or 0)


# --- reserve_attempt / release_attempt ---


async def test_a_reservation_under_the_limit_counts_and_starts_the_window(
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    retry_after = await reserve_attempt(redis_client, [IP_KEY, EMAIL_KEY], 5, 30)

    assert retry_after is None
    assert await _counter(redis_client, IP_KEY) == 1
    assert await _counter(redis_client, EMAIL_KEY) == 1
    assert 0 < await redis_client.ttl(IP_KEY) <= 30 * 60


async def test_a_refused_reservation_is_given_back_on_every_key(
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """The email is blocked: the request is refused before any password is checked, so the
    IP counter must not grow either — otherwise attempts on a blocked email would lock out
    everyone sharing that IP."""
    await redis_client.set(EMAIL_KEY, "5", ex=600)
    await redis_client.set(IP_KEY, "2", ex=600)

    retry_after = await reserve_attempt(redis_client, [IP_KEY, EMAIL_KEY], 5, 30)

    assert retry_after is not None
    assert await _counter(redis_client, EMAIL_KEY) == 5
    assert await _counter(redis_client, IP_KEY) == 2


async def test_a_refused_reservation_waits_for_the_longest_blocking_key(
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    await redis_client.set(IP_KEY, "5", ex=300)
    await redis_client.set(EMAIL_KEY, "5", ex=1500)

    retry_after = await reserve_attempt(redis_client, [IP_KEY, EMAIL_KEY], 5, 30)

    assert retry_after is not None
    assert retry_after > 300


async def test_releasing_gives_an_attempt_back(redis_client: fakeredis.FakeAsyncRedis) -> None:
    await redis_client.set(IP_KEY, "3", ex=600)

    await release_attempt(redis_client, [IP_KEY])

    assert await _counter(redis_client, IP_KEY) == 2


async def test_releasing_an_expired_counter_leaves_no_key(
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """The window ended while the request ran: a bare DECR would recreate the key at -1
    with no TTL, a free attempt that never expires."""
    await release_attempt(redis_client, [IP_KEY])

    assert await redis_client.exists(IP_KEY) == 0


# --- the parallel attack ---


async def test_parallel_failed_logins_are_capped_at_the_limit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the fix, every request read the counter while it was still under the limit:
    40 parallel logins gave 40 × 401 and no 429."""
    monkeypatch.setitem(
        app.dependency_overrides,
        get_auth_service,
        lambda: SlowFailingService(UnauthorizedError("Identifiants invalides")),
    )

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/login", json={"email": "test@gmail.com", "password": WRONG_PASSWORD}
            )
            for _ in range(PARALLEL_REQUESTS)
        )
    )

    statuses = [response.status_code for response in responses]
    assert statuses.count(401) == settings.LOGIN_RATE_LIMIT_MAX_ATTEMPTS
    assert statuses.count(429) == PARALLEL_REQUESTS - settings.LOGIN_RATE_LIMIT_MAX_ATTEMPTS


async def test_parallel_wrong_current_passwords_are_capped_at_the_limit(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = SlowFailingService(InvalidPasswordError("Mot de passe incorrect"))
    # The route also takes AuthService: replaced too, so that no request opens the shared
    # test session, which can't serve concurrent requests.
    monkeypatch.setitem(app.dependency_overrides, get_user_service, lambda: failing)
    monkeypatch.setitem(app.dependency_overrides, get_auth_service, lambda: failing)

    responses = await asyncio.gather(
        *(
            authenticated_client.patch(
                "/api/v1/users/me/email",
                json={"new_email": "new@test.com", "current_password": WRONG_PASSWORD},
            )
            for _ in range(PARALLEL_REQUESTS)
        )
    )

    statuses = [response.status_code for response in responses]
    assert statuses.count(403) == settings.SENSITIVE_RATE_LIMIT_MAX_ATTEMPTS
    assert statuses.count(429) == PARALLEL_REQUESTS - settings.SENSITIVE_RATE_LIMIT_MAX_ATTEMPTS


# --- what doesn't count ---


async def test_a_blocked_email_does_not_count_against_the_ip(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    await redis_client.set(EMAIL_KEY, str(settings.LOGIN_RATE_LIMIT_MAX_ATTEMPTS), ex=600)

    response = await client.post(
        "/api/v1/login", json={"email": "test@gmail.com", "password": WRONG_PASSWORD}
    )

    assert response.status_code == 429
    assert await _counter(redis_client, IP_KEY) == 0


async def test_an_unexpected_login_error_is_not_counted(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash is not a wrong password: the reserved attempt is given back."""
    monkeypatch.setitem(
        app.dependency_overrides, get_auth_service, lambda: SlowFailingService(RuntimeError())
    )

    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/login", json={"email": "test@gmail.com", "password": WRONG_PASSWORD}
        )

    assert await _counter(redis_client, IP_KEY) == 0
    assert await _counter(redis_client, EMAIL_KEY) == 0


async def test_an_invalid_body_on_a_sensitive_action_is_not_counted(
    authenticated_client: AsyncClient,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """The dependency doesn't read the body, so FastAPI runs it — and reserves — before
    answering 422 without calling the route: the reservation must still be given back."""
    response = await authenticated_client.patch(
        "/api/v1/users/me/password", json={"new_password": "new_password"}
    )

    assert response.status_code == 422
    assert await _counter(redis_client, sensitive_action_rate_limit_key(current_user.id)) == 0


async def test_another_refusal_on_a_sensitive_action_is_not_counted(
    authenticated_client: AsyncClient,
    current_user: User,
    other_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """Right password, but the new email is taken: only a wrong password counts."""
    # Read before the request: the route changes this same object, and the failed request's
    # rollback expires it.
    key = sensitive_action_rate_limit_key(current_user.id)

    response = await authenticated_client.patch(
        "/api/v1/users/me/email",
        json={"new_email": other_user.email, "current_password": PASSWORD},
    )

    assert response.status_code == 409
    assert await _counter(redis_client, key) == 0
