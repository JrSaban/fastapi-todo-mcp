"""Tests d'intégration du journal d'audit : une entrée par écriture, par ressource auditée."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, generate_api_token, hash_token
from src.modules.api_tokens.models import ApiToken
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.approvals.models import ApprovalAction, ApprovalRequest
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditLog, AuditResourceType
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate
from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate
from src.modules.users.models import User

TODOS_PATH = "/api/v1/todos"
CATEGORIES_PATH = "/api/v1/categories"
USERS_PATH = "/api/v1/users"
TOKENS_PATH = "/api/v1/api-tokens"
REQUESTS_PATH = "/api/v1/approval-requests"
PASSWORD = "secret123"  # the user fixtures' password


async def _entries(session: AsyncSession) -> Sequence[AuditLog]:
    result = await session.execute(select(AuditLog).order_by(AuditLog.id))
    return result.scalars().all()


async def _token(session: AsyncSession, owner_id: int) -> ApiToken:
    """An API token written through the repository: no audit entry of its own."""
    return await ApiTokenRepository(session).create(
        owner_id=owner_id,
        name="Agent",
        token_hash=hash_token(generate_api_token()),
        expires_at=None,
    )


async def _committed_token(session: AsyncSession, owner_id: int) -> tuple[ApiToken, str]:
    """A usable API token and its plain value, committed: a token request rolls back the
    single shared test connection, uncommitted setup included."""
    plain = generate_api_token()
    token = await ApiTokenRepository(session).create(
        owner_id=owner_id, name="Agent", token_hash=hash_token(plain), expires_at=None
    )
    await session.commit()
    return token, plain


async def _pending_request(
    session: AsyncSession,
    owner_id: int,
    todo_ids: list[int],
    *,
    expires_at: datetime | None = None,
) -> ApprovalRequest:
    return await ApprovalRequestRepository(session).create(
        owner_id=owner_id,
        action=ApprovalAction.DELETE_COMPLETED_TODOS,
        payload={"todo_ids": list(todo_ids)},
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=1),
    )


async def _todo(
    session: AsyncSession, owner_id: int, title: str = "Tâche", *, completed: bool = False
) -> Todo:
    """A todo written through the repository: it leaves no audit entry of its own."""
    todo = await TodoRepository(session).create(owner_id, TodoCreate(title=title), [])
    todo.is_completed = completed
    await session.flush()
    return todo


# --- one entry per write ---


async def test_creating_a_todo_records_who_created_it(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    response = await authenticated_client.post(TODOS_PATH, json={"title": "Nouvelle"})

    [entry] = await _entries(db_session)
    assert entry.action == AuditAction.CREATED
    assert entry.resource_type == AuditResourceType.TODO
    assert entry.resource_id == response.json()["id"]
    assert entry.resource_owner_id == current_user.id
    assert entry.actor_id == current_user.id
    assert entry.auth_method == AuditAuthMethod.SESSION
    assert entry.api_token_id is None
    assert entry.changed_fields is None


async def test_updating_records_only_the_fields_whose_value_changed(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The title sent unchanged isn't a change: only the completion is."""
    todo = await _todo(db_session, current_user.id, "Même titre")

    await authenticated_client.patch(
        f"{TODOS_PATH}/{todo.id}", json={"title": "Même titre", "is_completed": True}
    )

    [entry] = await _entries(db_session)
    assert entry.action == AuditAction.UPDATED
    assert entry.changed_fields == ["is_completed"]


async def test_an_update_that_changes_nothing_records_nothing(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    todo = await _todo(db_session, current_user.id, "Même titre")

    response = await authenticated_client.patch(
        f"{TODOS_PATH}/{todo.id}", json={"title": "Même titre"}
    )

    assert response.status_code == 200
    assert await _entries(db_session) == []


async def test_changing_the_categories_records_category_ids(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    category = await CategoryRepository(db_session).create(
        current_user.id, CategoryCreate(name="Maison")
    )
    todo = await _todo(db_session, current_user.id)

    await authenticated_client.patch(
        f"{TODOS_PATH}/{todo.id}", json={"category_ids": [category.id]}
    )

    [entry] = await _entries(db_session)
    assert entry.changed_fields == ["category_ids"]


async def test_unknown_category_ids_are_not_a_change(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Unknown IDs are silently dropped, so the todo still has no category: nothing changed."""
    todo = await _todo(db_session, current_user.id)

    await authenticated_client.patch(f"{TODOS_PATH}/{todo.id}", json={"category_ids": [999]})

    assert await _entries(db_session) == []


async def test_null_category_ids_is_not_a_change(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """`null` leaves the categories untouched, unlike `[]`: the request succeeds and nothing is
    recorded."""
    category = await CategoryRepository(db_session).create(
        current_user.id, CategoryCreate(name="Maison")
    )
    todo = await TodoRepository(db_session).create(
        current_user.id, TodoCreate(title="Tâche"), [category]
    )

    response = await authenticated_client.patch(
        f"{TODOS_PATH}/{todo.id}", json={"category_ids": None}
    )

    assert response.status_code == 200
    assert await _entries(db_session) == []


async def test_emptying_the_categories_records_category_ids(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """An empty list removes every category: that is a change."""
    category = await CategoryRepository(db_session).create(
        current_user.id, CategoryCreate(name="Maison")
    )
    todo = await TodoRepository(db_session).create(
        current_user.id, TodoCreate(title="Tâche"), [category]
    )

    await authenticated_client.patch(f"{TODOS_PATH}/{todo.id}", json={"category_ids": []})

    [entry] = await _entries(db_session)
    assert entry.changed_fields == ["category_ids"]


async def test_deleting_and_restoring_are_recorded(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    todo = await _todo(db_session, current_user.id)

    await authenticated_client.delete(f"{TODOS_PATH}/{todo.id}")
    await authenticated_client.post(f"{TODOS_PATH}/{todo.id}/restore")

    entries = await _entries(db_session)
    assert [entry.action for entry in entries] == [AuditAction.DELETED, AuditAction.RESTORED]
    assert {entry.resource_id for entry in entries} == {todo.id}


async def test_deleting_completed_todos_records_one_entry_per_deleted_todo(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Only the todos really deleted get an entry: an open one is left alone."""
    done_1 = await _todo(db_session, current_user.id, "Fait 1", completed=True)
    done_2 = await _todo(db_session, current_user.id, "Fait 2", completed=True)
    await _todo(db_session, current_user.id, "À faire")

    await authenticated_client.post(f"{TODOS_PATH}/delete-completed")

    entries = await _entries(db_session)
    assert {entry.resource_id for entry in entries} == {done_1.id, done_2.id}
    assert {entry.action for entry in entries} == {AuditAction.DELETED}


async def test_failed_writes_record_nothing(
    authenticated_client: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    """Someone else's todo is a 404: nothing was written, so nothing is recorded."""
    todo = await _todo(db_session, other_user.id)

    await authenticated_client.patch(f"{TODOS_PATH}/{todo.id}", json={"title": "Piraté"})
    await authenticated_client.delete(f"{TODOS_PATH}/{todo.id}")

    assert await _entries(db_session) == []


# --- who the entry belongs to, and who acted ---


async def test_an_admin_acting_on_someones_todo_is_recorded_for_its_owner(
    authenticated_admin: AsyncClient,
    db_session: AsyncSession,
    current_admin_user: User,
    other_user: User,
) -> None:
    """The entry belongs to the todo's owner, so they see what the admin did; the actor stays
    the admin."""
    todo = await _todo(db_session, other_user.id)

    await authenticated_admin.delete(f"{TODOS_PATH}/{todo.id}")

    [entry] = await _entries(db_session)
    assert entry.resource_owner_id == other_user.id
    assert entry.actor_id == current_admin_user.id


async def test_an_api_token_write_records_which_token_acted(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Through the real authentication: the actor comes from get_current_user itself."""
    plain = generate_api_token()
    token = await ApiTokenRepository(db_session).create(
        owner_id=current_user.id, name="Agent", token_hash=hash_token(plain), expires_at=None
    )
    await db_session.commit()

    await client.post(
        TODOS_PATH,
        json={"title": "Par l'agent"},
        headers={"Authorization": f"Bearer {plain}", "Idempotency-Key": "audit-1"},
    )

    [entry] = await _entries(db_session)
    assert entry.actor_id == current_user.id
    assert entry.auth_method == AuditAuthMethod.API_TOKEN
    assert entry.api_token_id == token.id


async def test_a_login_session_write_records_no_token(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Through a real access token rather than the fixture's override."""
    access = create_access_token({"sub": str(current_user.id)})

    await client.post(
        TODOS_PATH, json={"title": "Par l'humain"}, headers={"Authorization": f"Bearer {access}"}
    )

    [entry] = await _entries(db_session)
    assert entry.actor_id == current_user.id
    assert entry.auth_method == AuditAuthMethod.SESSION
    assert entry.api_token_id is None


# --- categories ---


async def test_category_writes_are_recorded_for_their_creator(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A category belongs to its creator: the entries are theirs, with the real changes."""
    created = await authenticated_client.post(CATEGORIES_PATH, json={"name": "Maison"})
    category_id = created.json()["id"]

    await authenticated_client.patch(
        f"{CATEGORIES_PATH}/{category_id}", json={"name": "Maison", "color": "#FF0000"}
    )

    created_entry, updated_entry = await _entries(db_session)
    assert created_entry.resource_type == AuditResourceType.CATEGORY
    assert created_entry.action == AuditAction.CREATED
    assert created_entry.resource_owner_id == current_user.id
    assert updated_entry.action == AuditAction.UPDATED
    assert updated_entry.changed_fields == ["color"]


async def test_a_category_update_that_changes_nothing_records_nothing(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    category = await CategoryRepository(db_session).create(
        current_user.id, CategoryCreate(name="Maison")
    )

    await authenticated_client.patch(f"{CATEGORIES_PATH}/{category.id}", json={"name": "Maison"})

    assert await _entries(db_session) == []


async def test_an_admin_deleting_a_category_is_recorded_for_its_creator(
    authenticated_admin: AsyncClient,
    db_session: AsyncSession,
    current_admin_user: User,
    other_user: User,
) -> None:
    category = await CategoryRepository(db_session).create(
        other_user.id, CategoryCreate(name="Sport")
    )

    await authenticated_admin.delete(f"{CATEGORIES_PATH}/{category.id}")

    [entry] = await _entries(db_session)
    assert entry.action == AuditAction.DELETED
    assert entry.resource_owner_id == other_user.id
    assert entry.actor_id == current_admin_user.id


# --- users ---


async def test_registration_is_recorded_without_an_actor(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A public registration has no authenticated actor: the entry says who was created, not
    who did it."""
    response = await client.post(
        USERS_PATH,
        json={
            "email": "new@test.com",
            "first_name": "New",
            "last_name": "User",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
    )

    [entry] = await _entries(db_session)
    assert entry.resource_type == AuditResourceType.USER
    assert entry.action == AuditAction.CREATED
    assert entry.resource_id == response.json()["id"]
    assert entry.resource_owner_id == response.json()["id"]
    assert entry.actor_id is None
    assert entry.auth_method is None


async def test_profile_update_records_only_the_changed_fields(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    await authenticated_client.patch(
        f"{USERS_PATH}/{current_user.id}",
        json={"first_name": current_user.first_name, "pseudo": "nouveau"},
    )

    [entry] = await _entries(db_session)
    assert entry.resource_owner_id == current_user.id
    assert entry.changed_fields == ["pseudo"]


async def test_email_and_password_changes_are_recorded_by_name_only(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The field's name, never its value: a password, even hashed, stays out of the log."""
    await authenticated_client.patch(
        f"{USERS_PATH}/me/email",
        json={"new_email": "changed@test.com", "current_password": PASSWORD},
    )
    await authenticated_client.patch(
        f"{USERS_PATH}/me/password",
        json={
            "old_password": PASSWORD,
            "new_password": "nouveau123",
            "confirm_new_password": "nouveau123",
        },
    )

    email_entry, password_entry = await _entries(db_session)
    assert email_entry.changed_fields == ["email"]
    assert password_entry.changed_fields == ["password"]


async def test_role_changes_are_recorded_for_the_target_account(
    authenticated_admin: AsyncClient,
    db_session: AsyncSession,
    current_admin_user: User,
    other_user: User,
) -> None:
    await authenticated_admin.patch(f"{USERS_PATH}/{other_user.id}/admin", json={"is_admin": True})
    await authenticated_admin.patch(
        f"{USERS_PATH}/{other_user.id}/active", json={"is_active": False}
    )

    admin_entry, active_entry = await _entries(db_session)
    assert admin_entry.changed_fields == ["is_admin"]
    assert active_entry.changed_fields == ["is_active"]
    assert {admin_entry.resource_owner_id, active_entry.resource_owner_id} == {other_user.id}
    assert {admin_entry.actor_id, active_entry.actor_id} == {current_admin_user.id}


async def test_setting_a_role_to_its_current_value_records_nothing(
    authenticated_admin: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    await authenticated_admin.patch(
        f"{USERS_PATH}/{other_user.id}/active", json={"is_active": True}
    )

    assert await _entries(db_session) == []


# --- API tokens ---


async def test_api_token_creation_and_revocation_are_recorded(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    created = await authenticated_client.post(
        TOKENS_PATH, json={"name": "Agent", "current_password": PASSWORD}
    )
    token_id = created.json()["id"]

    await authenticated_client.delete(f"{TOKENS_PATH}/{token_id}")

    created_entry, deleted_entry = await _entries(db_session)
    assert created_entry.resource_type == AuditResourceType.API_TOKEN
    assert [created_entry.action, deleted_entry.action] == [
        AuditAction.CREATED,
        AuditAction.DELETED,
    ]
    assert {created_entry.resource_id, deleted_entry.resource_id} == {token_id}
    assert created_entry.resource_owner_id == current_user.id


async def test_revoking_all_tokens_on_password_change_records_one_entry_per_token(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    first = await _token(db_session, current_user.id)
    second = await _token(db_session, current_user.id)

    await authenticated_client.patch(
        f"{USERS_PATH}/me/password",
        json={
            "old_password": PASSWORD,
            "new_password": "nouveau123",
            "confirm_new_password": "nouveau123",
            "revoke_api_tokens": True,
        },
    )

    deleted = [
        entry
        for entry in await _entries(db_session)
        if entry.resource_type == AuditResourceType.API_TOKEN
    ]
    assert {entry.resource_id for entry in deleted} == {first.id, second.id}
    assert {entry.action for entry in deleted} == {AuditAction.DELETED}


# --- approval requests ---


async def test_an_agents_bulk_request_is_recorded_with_its_token(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Which token asked for the bulk deletion is the trace the user needs most."""
    await _todo(db_session, current_user.id, "Fait", completed=True)
    token, plain = await _committed_token(db_session, current_user.id)

    response = await client.post(
        f"{TODOS_PATH}/delete-completed",
        headers={"Authorization": f"Bearer {plain}", "Idempotency-Key": "bulk-1"},
    )

    [entry] = await _entries(db_session)
    assert entry.resource_type == AuditResourceType.APPROVAL_REQUEST
    assert entry.action == AuditAction.CREATED
    assert entry.resource_id == response.json()["id"]
    assert entry.auth_method == AuditAuthMethod.API_TOKEN
    assert entry.api_token_id == token.id


async def test_approving_records_the_decision_and_each_deleted_todo(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    done_1 = await _todo(db_session, current_user.id, "Fait 1", completed=True)
    done_2 = await _todo(db_session, current_user.id, "Fait 2", completed=True)
    request = await _pending_request(db_session, current_user.id, [done_1.id, done_2.id])

    await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    entries = await _entries(db_session)
    deleted = [e for e in entries if e.resource_type == AuditResourceType.TODO]
    [approved] = [e for e in entries if e.resource_type == AuditResourceType.APPROVAL_REQUEST]
    assert {e.resource_id for e in deleted} == {done_1.id, done_2.id}
    assert {e.action for e in deleted} == {AuditAction.DELETED}
    assert approved.action == AuditAction.APPROVED
    assert approved.resource_id == request.id
    assert approved.auth_method == AuditAuthMethod.SESSION


async def test_a_rejection_tells_the_agent_from_the_user(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The question the approval task left open: a request cancelled by the agent carries the
    API token, one rejected by the user wouldn't."""
    request = await _pending_request(db_session, current_user.id, [])
    token, plain = await _committed_token(db_session, current_user.id)

    await client.post(
        f"{REQUESTS_PATH}/{request.id}/reject", headers={"Authorization": f"Bearer {plain}"}
    )

    [entry] = await _entries(db_session)
    assert entry.action == AuditAction.REJECTED
    assert entry.auth_method == AuditAuthMethod.API_TOKEN
    assert entry.api_token_id == token.id


async def test_a_failed_decision_records_nothing(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """An expired request can't be approved: the 409 writes no entry."""
    request = await _pending_request(
        db_session, current_user.id, [], expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert response.status_code == 409
    assert await _entries(db_session) == []
