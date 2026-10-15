"""Tests d'intégration des jetons d'API : gestion, authentification, middlewares et pruning."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.security import (
    API_TOKEN_PREFIX,
    create_access_token,
    generate_api_token,
    hash_token,
)
from src.modules.api_tokens.models import ApiToken
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.api_tokens.tasks import prune_expired_api_tokens
from src.modules.auth.router import sensitive_action_rate_limit_key
from src.modules.users.models import User
from src.modules.users.repository import UserRepository

TOKENS_PATH = "/api/v1/api-tokens"
ME_PATH = "/api/v1/users/me"
TODOS_PATH = "/api/v1/todos"
CATEGORIES_PATH = "/api/v1/categories"
IP_KEY = "rate_limit:general:ip:127.0.0.1"
PASSWORD = "secret123"  # the current_user / current_admin_user fixtures' password


async def _issue_token(
    session: AsyncSession,
    owner_id: int,
    *,
    expires_at: datetime | None = None,
    last_used_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Store a token straight through the repository; return the row and its plain value.

    Committed: a request carrying an API token makes the middlewares open their own session,
    and closing it rolls back the single shared test connection, uncommitted rows included.
    """
    plain = generate_api_token()
    token = await ApiTokenRepository(session).create(
        owner_id=owner_id, name="Agent", token_hash=hash_token(plain), expires_at=expires_at
    )
    if last_used_at is not None:
        token.last_used_at = last_used_at
    await session.commit()
    return token, plain


def _bearer(plain: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plain}"}


def _as_utc(value: str) -> datetime:
    """SQLite gives timestamps back without a timezone; they are UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _last_used_at(session: AsyncSession, token_id: int) -> datetime | None:
    result = await session.execute(select(ApiToken.last_used_at).where(ApiToken.id == token_id))
    return result.scalar_one()


# --- POST /api-tokens ---


async def test_create_returns_the_plain_token_once(
    authenticated_client: AsyncClient, db_session: AsyncSession
) -> None:
    """201 with the plain token, prefixed; only its hash is stored, and the list never
    shows it again."""
    response = await authenticated_client.post(
        TOKENS_PATH, json={"name": "Claude Code", "current_password": PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith(API_TOKEN_PREFIX)
    stored = await db_session.get(ApiToken, body["id"])
    assert stored is not None
    assert stored.token_hash == hash_token(body["token"])
    assert stored.token_hash != body["token"]

    listed = (await authenticated_client.get(TOKENS_PATH)).json()
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


@pytest.mark.parametrize(("expires_in_days", "expected_days"), [(None, None), (30, 30)])
async def test_create_expiry(
    authenticated_client: AsyncClient, expires_in_days: int | None, expected_days: int | None
) -> None:
    """null never expires; a number of days sets expires_at that far ahead."""
    response = await authenticated_client.post(
        TOKENS_PATH,
        json={"name": "Agent", "current_password": PASSWORD, "expires_in_days": expires_in_days},
    )

    expires_at = response.json()["expires_at"]
    if expected_days is None:
        assert expires_at is None
    else:
        delta = _as_utc(expires_at) - datetime.now(UTC)
        assert timedelta(days=expected_days - 1) < delta <= timedelta(days=expected_days)


async def test_create_defaults_to_90_days(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.post(
        TOKENS_PATH, json={"name": "Agent", "current_password": PASSWORD}
    )

    delta = _as_utc(response.json()["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=89) < delta <= timedelta(days=90)


async def test_create_with_wrong_password_counts_toward_the_limit(
    authenticated_client: AsyncClient,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """403, and the attempt counts in the counter shared with email/password changes."""
    response = await authenticated_client.post(
        TOKENS_PATH, json={"name": "Agent", "current_password": "wrong-password"}
    )

    assert response.status_code == 403
    assert await redis_client.get(sensitive_action_rate_limit_key(current_user.id)) == "1"


async def test_admin_cannot_create_a_token_and_is_not_counted(
    authenticated_admin: AsyncClient,
    current_admin_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """An admin is refused even with the right password, and the refusal is not mistaken
    for a wrong password: the sensitive-action counter stays untouched."""
    response = await authenticated_admin.post(
        TOKENS_PATH, json={"name": "Agent", "current_password": PASSWORD}
    )

    assert response.status_code == 403
    assert await redis_client.get(sensitive_action_rate_limit_key(current_admin_user.id)) is None


async def test_create_beyond_the_active_limit_returns_409(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    for _ in range(settings.API_TOKEN_MAX_ACTIVE):
        await _issue_token(db_session, current_user.id)

    response = await authenticated_client.post(
        TOKENS_PATH, json={"name": "One too many", "current_password": PASSWORD}
    )

    assert response.status_code == 409


async def test_expired_tokens_do_not_count_toward_the_limit(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    for _ in range(settings.API_TOKEN_MAX_ACTIVE):
        await _issue_token(db_session, current_user.id, expires_at=yesterday)

    response = await authenticated_client.post(
        TOKENS_PATH, json={"name": "Fresh", "current_password": PASSWORD}
    )

    assert response.status_code == 201


async def test_an_api_token_cannot_create_tokens(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Only a real login session can mint tokens: an agent can't make itself more."""
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        TOKENS_PATH,
        json={"name": "Agent copy", "current_password": PASSWORD},
        headers=_bearer(plain),
    )

    assert response.status_code == 403


async def test_an_api_token_cannot_revoke_tokens(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """An agent can't revoke its owner's other tokens (or its own): revoking needs a real
    login session, like creating."""
    _, plain = await _issue_token(db_session, current_user.id)
    other, _ = await _issue_token(db_session, current_user.id)

    response = await client.delete(f"{TOKENS_PATH}/{other.id}", headers=_bearer(plain))

    assert response.status_code == 403
    assert await ApiTokenRepository(db_session).get_by_id(other.id) is not None


async def test_an_api_token_can_list_tokens(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Listing stays open to a token: it shows names and dates, never a token's value."""
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.get(TOKENS_PATH, headers=_bearer(plain))

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_an_api_token_cannot_create_a_category(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A category's name is read by every user's agent: an agent steered by injected text could
    otherwise copy the injection into a new category, or publish its owner's private todos."""
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        CATEGORIES_PATH,
        json={"name": "From an agent"},
        headers={**_bearer(plain), "Idempotency-Key": "category-1"},
    )
    categories = await client.get(CATEGORIES_PATH, headers=_bearer(plain))

    assert response.status_code == 403
    assert categories.status_code == 200
    assert categories.json()["total"] == 0


async def test_a_login_session_still_creates_categories(
    client: AsyncClient, current_user: User
) -> None:
    """Only API tokens are refused: a real access token goes through."""
    token = create_access_token({"sub": str(current_user.id)})

    response = await client.post(CATEGORIES_PATH, json={"name": "Human"}, headers=_bearer(token))

    assert response.status_code == 201


# --- GET /api-tokens and DELETE /api-tokens/{id} ---


async def test_list_shows_only_your_tokens(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    own, _ = await _issue_token(db_session, current_user.id)
    await _issue_token(db_session, other_user.id)

    response = await authenticated_client.get(TOKENS_PATH)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [own.id]


async def test_revoke_deletes_the_token(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    token, _ = await _issue_token(db_session, current_user.id)

    response = await authenticated_client.delete(f"{TOKENS_PATH}/{token.id}")

    assert response.status_code == 204
    assert await ApiTokenRepository(db_session).get_by_id(token.id) is None


async def test_revoking_someone_elses_token_returns_404(
    authenticated_client: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    token, _ = await _issue_token(db_session, other_user.id)

    response = await authenticated_client.delete(f"{TOKENS_PATH}/{token.id}")

    assert response.status_code == 404


# --- authenticating with an API token ---


async def test_valid_token_authenticates_its_owner(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 200
    assert response.json()["id"] == current_user.id


async def test_unknown_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(ME_PATH, headers=_bearer(generate_api_token()))

    assert response.status_code == 401


async def test_expired_token_returns_401(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    _, plain = await _issue_token(
        db_session, current_user.id, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 401


async def test_revoked_token_no_longer_authenticates(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    token, plain = await _issue_token(db_session, current_user.id)
    await ApiTokenRepository(db_session).delete(token)
    await db_session.commit()

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 401


async def test_token_of_a_deactivated_user_returns_401(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    _, plain = await _issue_token(db_session, current_user.id)
    await UserRepository(db_session).set_active(current_user, False)
    await db_session.commit()

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 401


async def test_token_of_an_admin_is_refused(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A token never authenticates an admin, including one promoted after its creation."""
    _, plain = await _issue_token(db_session, current_user.id)
    await UserRepository(db_session).set_admin(current_user, True)
    await db_session.commit()

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 403


@pytest.mark.parametrize(
    "route",
    [
        ("PATCH", f"{ME_PATH}/email", {"new_email": "new@test.com", "current_password": PASSWORD}),
        (
            "PATCH",
            f"{ME_PATH}/password",
            {
                "old_password": PASSWORD,
                "new_password": "new_password",
                "confirm_new_password": "new_password",
            },
        ),
        ("PATCH", "/api/v1/users/{user_id}", {"first_name": "Agent"}),
        ("POST", "/api/v1/logout", None),
    ],
)
async def test_account_management_refuses_api_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    route: tuple[str, str, dict[str, str] | None],
) -> None:
    """Managing the account needs a real login session, even with the right password: an agent
    can't change the email, the password or the profile, nor log the human out."""
    method, path, body = route
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.request(
        method, path.format(user_id=current_user.id), json=body, headers=_bearer(plain)
    )

    assert response.status_code == 403


async def test_first_use_records_last_used_at(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    token, plain = await _issue_token(db_session, current_user.id)

    await client.get(ME_PATH, headers=_bearer(plain))

    assert await _last_used_at(db_session, token.id) is not None


async def test_last_used_at_is_not_rewritten_within_the_hour(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """At most one write an hour: a use 10 minutes after the last one leaves it alone."""
    token, plain = await _issue_token(
        db_session, current_user.id, last_used_at=datetime.now(UTC) - timedelta(minutes=10)
    )
    before = await _last_used_at(db_session, token.id)

    await client.get(ME_PATH, headers=_bearer(plain))

    assert await _last_used_at(db_session, token.id) == before


async def test_last_used_at_is_refreshed_after_an_hour(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    token, plain = await _issue_token(
        db_session, current_user.id, last_used_at=datetime.now(UTC) - timedelta(hours=2)
    )
    before = await _last_used_at(db_session, token.id)

    await client.get(ME_PATH, headers=_bearer(plain))

    after = await _last_used_at(db_session, token.id)
    assert before is not None and after is not None
    assert after > before


# --- rate limiting and idempotency with an API token ---


async def test_api_token_request_is_counted_per_user(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """An agent shares its owner's general quota, like a JWT request, not the IP's."""
    _, plain = await _issue_token(db_session, current_user.id)

    await client.get(ME_PATH, headers=_bearer(plain))

    assert await redis_client.get(f"rate_limit:general:user:{current_user.id}") == "1"
    assert await redis_client.get(IP_KEY) is None


@pytest.mark.parametrize("token_state", ["unknown", "expired"])
async def test_unusable_api_token_is_counted_per_ip(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
    token_state: str,
) -> None:
    """A token is looked up, not trusted: random `tda_` values would otherwise each get a
    fresh counter and bypass the IP limit."""
    if token_state == "unknown":
        plain = generate_api_token()
    else:
        _, plain = await _issue_token(
            db_session, current_user.id, expires_at=datetime.now(UTC) - timedelta(minutes=1)
        )

    await client.get(ME_PATH, headers=_bearer(plain))

    assert await redis_client.get(IP_KEY) == "1"


async def test_identity_lookup_failure_falls_back_to_ip(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    redis_client: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the middleware's lookup fails, the request goes on, counted by IP."""
    _, plain = await _issue_token(db_session, current_user.id)
    monkeypatch.setattr(
        "src.main.ApiTokenRepository.get_usable_owner_id",
        AsyncMock(side_effect=OSError("database down")),
    )

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 200
    assert await redis_client.get(IP_KEY) == "1"


async def test_idempotency_works_with_an_api_token(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The idempotency middleware sees the token's owner: a retry is replayed, not
    duplicated."""
    _, plain = await _issue_token(db_session, current_user.id)
    headers = {**_bearer(plain), "Idempotency-Key": "agent-retry-1"}

    first = await client.post(TODOS_PATH, json={"title": "From an agent"}, headers=headers)
    second = await client.post(TODOS_PATH, json={"title": "From an agent"}, headers=headers)

    # Proven by the replay itself rather than a row count: in tests the request session is
    # never committed, and the next API-token lookup rolls back the shared connection.
    assert first.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()


async def test_identity_is_looked_up_once_per_request(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rate limiting and idempotency share one lookup through request.state."""
    _, plain = await _issue_token(db_session, current_user.id)
    lookup = AsyncMock(return_value=current_user.id)
    monkeypatch.setattr("src.main.ApiTokenRepository.get_usable_owner_id", lookup)

    await client.post(
        TODOS_PATH,
        json={"title": "Once"},
        headers={**_bearer(plain), "Idempotency-Key": "single-lookup"},
    )

    assert lookup.await_count == 1


@pytest.mark.parametrize(
    ("path", "body"), [(TODOS_PATH, {"title": "No key"}), (f"{TODOS_PATH}/delete-completed", {})]
)
async def test_api_token_create_without_idempotency_key_returns_400(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    path: str,
    body: dict[str, str],
) -> None:
    """An agent must send an Idempotency-Key on the idempotent routes, so a blind retry
    can never create a duplicate."""
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.post(path, json=body, headers=_bearer(plain))

    assert response.status_code == 400


async def test_access_token_create_without_idempotency_key_still_works(
    client: AsyncClient, current_user: User
) -> None:
    """The key stays optional for a human session (JWT)."""
    token = create_access_token({"sub": str(current_user.id)})

    response = await client.post(TODOS_PATH, json={"title": "Human"}, headers=_bearer(token))

    assert response.status_code == 201


async def test_api_token_read_needs_no_idempotency_key(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Only the idempotent POST routes require the key."""
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.get(TODOS_PATH, headers=_bearer(plain))

    assert response.status_code == 200


async def test_unknown_api_token_without_key_gets_401_not_400(client: AsyncClient) -> None:
    """With no usable token the middleware steps aside: the route's 401 wins, the request
    isn't told about a key it couldn't use anyway."""
    response = await client.post(
        TODOS_PATH, json={"title": "No key"}, headers=_bearer(generate_api_token())
    )

    assert response.status_code == 401


# --- PATCH /users/me/password with revoke_api_tokens ---


@pytest.mark.parametrize(("revoke", "remaining"), [(True, 0), (False, 2)])
async def test_password_change_revokes_tokens_only_on_request(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    revoke: bool,
    remaining: int,
) -> None:
    """Tokens survive a password change by default; `revoke_api_tokens` deletes them all."""
    await _issue_token(db_session, current_user.id)
    await _issue_token(db_session, current_user.id)

    response = await authenticated_client.patch(
        f"{ME_PATH}/password",
        json={
            "old_password": PASSWORD,
            "new_password": "new_password",
            "confirm_new_password": "new_password",
            "revoke_api_tokens": revoke,
        },
    )

    assert response.status_code == 200
    tokens = await ApiTokenRepository(db_session).list_for_owner(current_user.id)
    assert len(tokens) == remaining


# --- api_tokens:prune_expired ---


async def test_prune_deletes_only_tokens_expired_beyond_retention(
    db_session: AsyncSession, current_user: User
) -> None:
    """Expired for longer than API_TOKEN_EXPIRED_RETENTION_DAYS → deleted. Recently expired
    (still shown to the user as expired), still valid and never-expiring tokens stay."""
    now = datetime.now(UTC)
    retention = timedelta(days=settings.API_TOKEN_EXPIRED_RETENTION_DAYS)
    long_expired, _ = await _issue_token(
        db_session, current_user.id, expires_at=now - retention - timedelta(days=1)
    )
    recently_expired, _ = await _issue_token(
        db_session, current_user.id, expires_at=now - timedelta(days=1)
    )
    valid, _ = await _issue_token(db_session, current_user.id, expires_at=now + timedelta(days=1))
    never_expiring, _ = await _issue_token(db_session, current_user.id)

    result = await (await prune_expired_api_tokens.kiq()).wait_result()

    assert result.return_value == {"api_tokens": 1}
    remaining = {
        token.id for token in await ApiTokenRepository(db_session).list_for_owner(current_user.id)
    }
    assert remaining == {recently_expired.id, valid.id, never_expiring.id}
    assert long_expired.id not in remaining


def test_prune_is_scheduled_weekly() -> None:
    """Sunday 05:00 UTC, after the two maintenance prunings."""
    assert prune_expired_api_tokens.labels["schedule"] == [{"cron": "0 5 * * 0"}]
