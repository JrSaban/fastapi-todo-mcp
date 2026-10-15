"""Tests d'intégration du middleware d'idempotence (header Idempotency-Key)."""

import json
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.core.idempotency import hash_request, idempotency_redis_key
from src.core.security import create_access_token
from src.main import app
from src.modules.users.models import User

TODOS_PATH = "/api/v1/todos"
CATEGORIES_PATH = "/api/v1/categories"
KEY = "3f1c9a2e-key"
# Sent as raw bytes rather than json=..., so a test can hash exactly what the server receives.
TODO_BODY = b'{"title": "Buy milk"}'


def _headers(user_id: int, key: str = KEY) -> dict[str, str]:
    """A real access token (the middleware reads the JWT itself) plus the idempotency key."""
    token = create_access_token({"sub": str(user_id)})
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }


async def _post_todo(
    client: AsyncClient, user_id: int, key: str = KEY, body: bytes = TODO_BODY
) -> Response:
    return await client.post(TODOS_PATH, content=body, headers=_headers(user_id, key))


async def _todo_count(client: AsyncClient) -> int:
    response = await client.get(TODOS_PATH)
    return response.json()["total"]


async def test_replay_returns_stored_response_without_duplicate(
    authenticated_client: AsyncClient, current_user: User
) -> None:
    """Same key, same body → the second request gets the first response back, flagged
    as a replay, and no second todo is created."""
    first = await _post_todo(authenticated_client, current_user.id)
    second = await _post_todo(authenticated_client, current_user.id)

    assert first.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()
    assert await _todo_count(authenticated_client) == 1


async def test_lowercase_bearer_scheme_is_still_deduplicated(
    authenticated_client: AsyncClient, current_user: User
) -> None:
    """The route accepts `bearer` in lowercase: the middleware must see the user too, or the
    client would believe it is protected from duplicates while two todos get created."""
    headers = _headers(current_user.id)
    headers["Authorization"] = headers["Authorization"].replace("Bearer ", "bearer ", 1)

    await authenticated_client.post(TODOS_PATH, content=TODO_BODY, headers=headers)
    second = await authenticated_client.post(TODOS_PATH, content=TODO_BODY, headers=headers)

    assert second.headers["Idempotent-Replayed"] == "true"
    assert await _todo_count(authenticated_client) == 1


async def test_without_header_requests_are_not_deduplicated(
    authenticated_client: AsyncClient, current_user: User
) -> None:
    """The header is optional: without it, two identical POSTs create two todos."""
    headers = _headers(current_user.id)
    del headers["Idempotency-Key"]

    await authenticated_client.post(TODOS_PATH, content=TODO_BODY, headers=headers)
    await authenticated_client.post(TODOS_PATH, content=TODO_BODY, headers=headers)

    assert await _todo_count(authenticated_client) == 2


async def test_same_key_with_different_body_returns_422(
    authenticated_client: AsyncClient, current_user: User
) -> None:
    """Reusing a key for another payload is a client error, not a replay."""
    await _post_todo(authenticated_client, current_user.id)

    response = await _post_todo(
        authenticated_client, current_user.id, body=b'{"title": "Something else"}'
    )

    assert response.status_code == 422
    assert await _todo_count(authenticated_client) == 1


async def test_same_key_on_another_endpoint_returns_422(
    authenticated_client: AsyncClient, current_user: User
) -> None:
    """The hash covers the method and the path, not just the body: a key reused on another
    route is a different request (422), never a replay of the other route's resource."""
    # Valid for both schemas, each ignoring the other's field: only the route differs.
    body = b'{"title": "Sport", "name": "Sport"}'
    await authenticated_client.post(TODOS_PATH, content=body, headers=_headers(current_user.id))

    response = await authenticated_client.post(
        CATEGORIES_PATH, content=body, headers=_headers(current_user.id)
    )

    assert response.status_code == 422
    assert "Idempotent-Replayed" not in response.headers
    categories = await authenticated_client.get(CATEGORIES_PATH)
    assert categories.json()["total"] == 0


async def test_key_in_progress_returns_409_with_retry_after(
    authenticated_client: AsyncClient,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """A retry arriving while the first request is still being processed is refused
    with 409 and told when to come back, instead of running a second time."""
    redis_key = idempotency_redis_key(user_id=str(current_user.id), idempotency_key=KEY)
    request_hash = hash_request(method="POST", path=TODOS_PATH, body=TODO_BODY)
    claim = {"status": "in_progress", "request_hash": request_hash}
    await redis_client.set(redis_key, json.dumps(claim), ex=30)

    response = await _post_todo(authenticated_client, current_user.id)

    assert response.status_code == 409
    assert 0 < int(response.headers["Retry-After"]) <= 30
    assert await _todo_count(authenticated_client) == 0


async def test_key_vanishing_between_claim_and_read_returns_409(
    authenticated_client: AsyncClient, current_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim fails but the key is gone by the time it's read (expired or released
    in between): the request must not run without holding the claim."""
    monkeypatch.setattr("src.main.claim_idempotency_key", AsyncMock(return_value=False))
    monkeypatch.setattr("src.main.get_idempotency_record", AsyncMock(return_value=None))

    response = await _post_todo(authenticated_client, current_user.id)

    assert response.status_code == 409
    assert await _todo_count(authenticated_client) == 0


async def test_error_response_is_not_stored(
    authenticated_client: AsyncClient,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """A non-2xx answer depends on state that may change (here, a taken category name):
    the key is released so that a retry really runs again."""
    await authenticated_client.post(CATEGORIES_PATH, json={"name": "Work"})

    response = await authenticated_client.post(
        CATEGORIES_PATH, content=b'{"name": "Work"}', headers=_headers(current_user.id)
    )

    assert response.status_code == 409
    redis_key = idempotency_redis_key(user_id=str(current_user.id), idempotency_key=KEY)
    assert await redis_client.get(redis_key) is None


async def test_unhandled_exception_releases_the_key(
    authenticated_client: AsyncClient,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception escaping the route must not leave the key stuck in_progress."""
    monkeypatch.setattr(
        "src.modules.todos.service.TodoService.create_todo",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError):
        await _post_todo(authenticated_client, current_user.id)

    redis_key = idempotency_redis_key(user_id=str(current_user.id), idempotency_key=KEY)
    assert await redis_client.get(redis_key) is None


async def test_failed_commit_is_not_stored(
    authenticated_client: AsyncClient,
    current_user: User,
    db_session: AsyncSession,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """The response is only stored once the transaction has committed: a failed commit
    answers 500 and leaves nothing to replay."""

    async def session_with_failing_commit() -> AsyncGenerator[AsyncSession, None]:
        yield db_session
        raise RuntimeError("commit failed")

    app.dependency_overrides[get_db_session] = session_with_failing_commit
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await _post_todo(client, current_user.id)

    assert response.status_code == 500
    redis_key = idempotency_redis_key(user_id=str(current_user.id), idempotency_key=KEY)
    assert await redis_client.get(redis_key) is None


async def test_keys_are_scoped_per_user(
    authenticated_client: AsyncClient, current_user: User, other_user: User
) -> None:
    """Another user sending the same key gets a fresh request, never someone else's
    stored response."""
    first = await _post_todo(authenticated_client, current_user.id)
    # The route still creates for current_user (overridden get_current_user); what matters
    # is that the middleware, reading other_user's token, doesn't treat it as a replay.
    second = await _post_todo(authenticated_client, other_user.id)

    assert second.status_code == 201
    assert "Idempotent-Replayed" not in second.headers
    assert second.json()["id"] != first.json()["id"]


@pytest.mark.parametrize("key", ["", "k" * 256])
async def test_invalid_key_returns_400(
    authenticated_client: AsyncClient, current_user: User, key: str
) -> None:
    """A key that is present but empty or longer than 255 characters is rejected rather
    than silently ignored, so the client doesn't believe it is protected."""
    response = await _post_todo(authenticated_client, current_user.id, key=key)

    assert response.status_code == 400
    assert await _todo_count(authenticated_client) == 0


async def test_key_without_valid_token_is_ignored(
    client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """With no user to scope the key to, the middleware steps aside and the route
    answers its usual 401; nothing is claimed in Redis."""
    response = await client.post(TODOS_PATH, content=TODO_BODY, headers={"Idempotency-Key": KEY})

    assert response.status_code == 401
    assert await redis_client.keys("idempotency:*") == []


async def test_redis_unavailable_returns_503(
    authenticated_client: AsyncClient, current_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client asked for a no-duplicate guarantee that can't be given without Redis:
    refuse with 503 instead of processing unprotected."""
    broken_redis = MagicMock()
    broken_redis.incr = AsyncMock(side_effect=RedisError("Redis is down"))
    broken_redis.set = AsyncMock(side_effect=RedisError("Redis is down"))
    monkeypatch.setattr("src.core.redis.redis_client", broken_redis)

    response = await _post_todo(authenticated_client, current_user.id)

    assert response.status_code == 503
    assert await _todo_count(authenticated_client) == 0
