"""Tests d'intégration de la lecture du journal d'audit et de sa purge."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.security import generate_api_token, hash_token
from src.modules.api_tokens.models import ApiTokenUsage
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditLog, AuditResourceType
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.tasks import prune_old_audit_logs
from src.modules.users.models import User

AUDIT_PATH = "/api/v1/audit-logs"


async def _entry(
    session: AsyncSession,
    owner_id: int,
    *,
    action: AuditAction = AuditAction.CREATED,
    resource_type: AuditResourceType = AuditResourceType.TODO,
    resource_id: int = 1,
    actor_id: int | None = None,
    api_token_id: int | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    """An entry written straight through the repository."""
    entry = await AuditLogRepository(session).create(
        actor_id=actor_id,
        auth_method=AuditAuthMethod.API_TOKEN if api_token_id else AuditAuthMethod.SESSION,
        api_token_id=api_token_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_owner_id=owner_id,
        action=action,
        changed_fields=["title"] if action == AuditAction.UPDATED else None,
    )
    if created_at is not None:
        entry.created_at = created_at
        await session.flush()
    return entry


def _ids(response) -> list[int]:
    return [item["id"] for item in response.json()["items"]]


# --- GET /audit-logs ---


async def test_a_user_sees_only_the_entries_about_their_resources(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    """Their `owner_id` filter is ignored, like on every other list: it can't reach another
    user's entries."""
    mine = await _entry(db_session, current_user.id, actor_id=current_user.id)
    await _entry(db_session, other_user.id, actor_id=other_user.id)

    response = await authenticated_client.get(f"{AUDIT_PATH}?owner_id={other_user.id}")

    assert response.status_code == 200
    assert _ids(response) == [mine.id]
    assert response.json()["total"] == 1


async def test_a_user_sees_what_an_admin_did_on_their_resources(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    current_admin_user: User,
) -> None:
    """The entry is theirs as the resource's owner, and shows the admin as the actor."""
    entry = await _entry(
        db_session, current_user.id, action=AuditAction.DELETED, actor_id=current_admin_user.id
    )

    response = await authenticated_client.get(AUDIT_PATH)

    [item] = response.json()["items"]
    assert item["id"] == entry.id
    assert item["actor_id"] == current_admin_user.id
    assert item["resource_owner_id"] == current_user.id


async def test_an_admin_sees_everyone_and_filters_by_owner(
    authenticated_admin: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    await _entry(db_session, current_user.id)
    theirs = await _entry(db_session, other_user.id)

    everyone = await authenticated_admin.get(AUDIT_PATH)
    filtered = await authenticated_admin.get(f"{AUDIT_PATH}?owner_id={other_user.id}")

    assert everyone.json()["total"] == 2
    assert _ids(filtered) == [theirs.id]


async def test_most_recent_entries_come_first(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    now = datetime.now(UTC)
    older = await _entry(db_session, current_user.id, created_at=now - timedelta(hours=1))
    newer = await _entry(db_session, current_user.id, created_at=now)

    response = await authenticated_client.get(AUDIT_PATH)

    assert _ids(response) == [newer.id, older.id]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("action=deleted", "deleted"),
        ("resource_type=category", "category"),
        ("resource_id=42", "todo_42"),
        ("api_token_id=7", "by_token"),
    ],
)
async def test_the_filters_select_the_matching_entries_and_their_total(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    query: str,
    expected: str,
) -> None:
    """Each filter narrows both the page and its total."""
    entries = {
        "deleted": await _entry(db_session, current_user.id, action=AuditAction.DELETED),
        "category": await _entry(
            db_session, current_user.id, resource_type=AuditResourceType.CATEGORY
        ),
        "todo_42": await _entry(db_session, current_user.id, resource_id=42),
        "by_token": await _entry(db_session, current_user.id, api_token_id=7),
    }

    response = await authenticated_client.get(f"{AUDIT_PATH}?{query}")

    assert _ids(response) == [entries[expected].id]
    assert response.json()["total"] == 1


async def test_an_entry_shows_how_the_actor_authenticated_and_what_changed(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The list carries what the log is read for: human or agent, and which fields."""
    await _entry(db_session, current_user.id, action=AuditAction.UPDATED, api_token_id=7)

    [item] = (await authenticated_client.get(AUDIT_PATH)).json()["items"]

    assert item["auth_method"] == "api_token"
    assert item["api_token_id"] == 7
    assert item["changed_fields"] == ["title"]


async def test_an_unknown_filter_value_returns_422(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.get(f"{AUDIT_PATH}?action=viewed")

    assert response.status_code == 422


# --- GET /audit-logs/{id} ---


async def test_get_own_entry(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    entry = await _entry(db_session, current_user.id)

    response = await authenticated_client.get(f"{AUDIT_PATH}/{entry.id}")

    assert response.status_code == 200
    assert response.json()["id"] == entry.id


@pytest.mark.parametrize("missing", ["someone_elses", "unknown"])
async def test_someone_elses_or_an_unknown_entry_returns_404(
    authenticated_client: AsyncClient, db_session: AsyncSession, other_user: User, missing: str
) -> None:
    entry_id = (await _entry(db_session, other_user.id)).id if missing == "someone_elses" else 999

    response = await authenticated_client.get(f"{AUDIT_PATH}/{entry_id}")

    assert response.status_code == 404


async def test_an_admin_gets_anyones_entry(
    authenticated_admin: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    entry = await _entry(db_session, other_user.id)

    response = await authenticated_admin.get(f"{AUDIT_PATH}/{entry.id}")

    assert response.status_code == 200


# --- API tokens can't read the log ---


@pytest.mark.parametrize("path", ["", "/1"])
async def test_an_api_token_cannot_read_the_log(
    client: AsyncClient, db_session: AsyncSession, current_user: User, path: str
) -> None:
    """The log is the human's oversight of their agents: an agent has no use for it, and the
    boundary sits in the API, not only in the MCP server."""
    await _entry(db_session, current_user.id)
    plain = generate_api_token()
    await ApiTokenRepository(db_session).create(
        owner_id=current_user.id,
        name="Agent",
        usage=ApiTokenUsage.API,
        token_hash=hash_token(plain),
        expires_at=None,
    )
    await db_session.commit()

    response = await client.get(f"{AUDIT_PATH}{path}", headers={"Authorization": f"Bearer {plain}"})

    assert response.status_code == 403


# --- audit:prune_old ---


async def test_prune_deletes_entries_older_than_the_retention(
    db_session: AsyncSession, current_user: User
) -> None:
    now = datetime.now(UTC)
    retention = timedelta(days=settings.AUDIT_LOG_RETENTION_DAYS)
    await _entry(db_session, current_user.id, created_at=now - retention - timedelta(days=1))
    recent = await _entry(
        db_session, current_user.id, created_at=now - retention + timedelta(days=1)
    )
    await db_session.commit()

    result = await (await prune_old_audit_logs.kiq()).wait_result()

    assert result.return_value == {"audit_logs": 1}
    remaining = await db_session.execute(select(AuditLog.id))
    assert set(remaining.scalars()) == {recent.id}


def test_prune_is_scheduled_monthly() -> None:
    """The 1st of each month, 01:00 UTC."""
    assert prune_old_audit_logs.labels["schedule"] == [{"cron": "0 1 1 * *"}]
