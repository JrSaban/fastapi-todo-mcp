"""Tests d'intégration du journal d'audit : une entrée par écriture sur les todos."""

from collections.abc import Sequence

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import create_access_token, generate_api_token, hash_token
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditLog, AuditResourceType
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate
from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate
from src.modules.users.models import User

TODOS_PATH = "/api/v1/todos"


async def _entries(session: AsyncSession) -> Sequence[AuditLog]:
    result = await session.execute(select(AuditLog).order_by(AuditLog.id))
    return result.scalars().all()


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
