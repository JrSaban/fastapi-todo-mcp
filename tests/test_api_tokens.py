"""Tests d'intégration des jetons d'API : création, liste, révocation et authentification."""

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.security import API_TOKEN_PREFIX, generate_api_token, hash_token
from src.modules.api_tokens.models import ApiToken
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.auth.router import sensitive_action_rate_limit_key
from src.modules.users.models import User
from src.modules.users.repository import UserRepository

TOKENS_PATH = "/api/v1/api-tokens"
ME_PATH = "/api/v1/users/me"
PASSWORD = "secret123"  # the current_user / current_admin_user fixtures' password


async def _issue_token(
    session: AsyncSession,
    owner_id: int,
    *,
    expires_at: datetime | None = None,
    last_used_at: datetime | None = None,
) -> tuple[ApiToken, str]:
    """Store a token straight through the repository; return the row and its plain value."""
    plain = generate_api_token()
    token = await ApiTokenRepository(session).create(
        owner_id=owner_id, name="Agent", token_hash=hash_token(plain), expires_at=expires_at
    )
    if last_used_at is not None:
        token.last_used_at = last_used_at
        await session.flush()
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

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 401


async def test_token_of_a_deactivated_user_returns_401(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    _, plain = await _issue_token(db_session, current_user.id)
    await UserRepository(db_session).set_active(current_user, False)

    response = await client.get(ME_PATH, headers=_bearer(plain))

    assert response.status_code == 401


async def test_token_of_an_admin_is_refused(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A token never authenticates an admin, including one promoted after its creation."""
    _, plain = await _issue_token(db_session, current_user.id)
    await UserRepository(db_session).set_admin(current_user, True)

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
    ],
)
async def test_account_management_refuses_api_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    route: tuple[str, str, dict[str, str]],
) -> None:
    """Changing the email or password needs a real login session, even with the right password."""
    method, path, body = route
    _, plain = await _issue_token(db_session, current_user.id)

    response = await client.request(method, path, json=body, headers=_bearer(plain))

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
