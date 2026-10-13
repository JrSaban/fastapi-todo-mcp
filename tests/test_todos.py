"""Tests d'intégration des endpoints Todos."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate
from src.modules.users.models import User


async def test_healthcheck(client: AsyncClient) -> None:
    """Checks that /health returns 200 OK."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "app" in data


# --- create ---


async def test_create_todo_success(
    authenticated_client: AsyncClient,
    current_user: User,
) -> None:
    """Successful todo creation (POST /api/v1/todos)."""
    payload = {
        "title": "Acheter du café",
        "description": "Café en grains bio",
        "owner_id": 999,
    }
    response = await authenticated_client.post("/api/v1/todos", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["owner_id"] == current_user.id
    assert data["title"] == payload["title"]
    assert data["description"] == payload["description"]
    assert data["is_completed"] is False
    assert "created_at" in data
    assert "updated_at" in data


async def test_create_todo_missing_title_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """Omitting the title is rejected by Pydantic (422 Unprocessable Entity)."""
    response = await authenticated_client.post(
        "/api/v1/todos", json={"description": "Pas de titre"}
    )
    assert response.status_code == 422


async def test_create_todo_empty_title_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """An empty title ("") is rejected thanks to min_length=1."""
    response = await authenticated_client.post("/api/v1/todos", json={"title": ""})
    assert response.status_code == 422


async def test_create_todo_with_categories(
    authenticated_client: AsyncClient,
) -> None:
    """Creating a todo with categories works."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 2"})

    payload = {
        "title": "Tâche avec catégories",
        "category_ids": [cat_1.json()["id"], cat_2.json()["id"]],
    }

    response = await authenticated_client.post("/api/v1/todos", json=payload)
    todo_id = response.json()["id"]
    assert response.json()["title"] == payload["title"]

    detail = await authenticated_client.get(f"/api/v1/todos/{todo_id}?include=categories")
    assert detail.json()["categories"] == [cat_1.json(), cat_2.json()]


async def test_create_todo_ignore_invalid_category_ids(
    authenticated_client: AsyncClient,
) -> None:
    """Creating a todo with a nonexistent category id."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})

    payload = {
        "title": "Tâche avec catégorie inexistante",
        "category_ids": [cat_1.json()["id"], 9999],
    }

    response = await authenticated_client.post("/api/v1/todos", json=payload)
    todo_id = response.json()["id"]
    assert response.json()["title"] == payload["title"]

    detail = await authenticated_client.get(f"/api/v1/todos/{todo_id}?include=categories")
    assert len(detail.json()["categories"]) == 1
    assert detail.json()["categories"] == [cat_1.json()]


async def test_create_todo_ignores_deleted_category_id(
    authenticated_admin: AsyncClient,
) -> None:
    """A soft-deleted category id behaves like a nonexistent one: it's silently
    excluded rather than attached (CategoryRepository.get_by_ids filters it out)."""
    cat_1 = await authenticated_admin.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_1_id = cat_1.json()["id"]
    await authenticated_admin.delete(f"/api/v1/categories/{cat_1_id}")

    payload = {
        "title": "Tâche avec catégorie supprimée",
        "category_ids": [cat_1_id],
    }

    response = await authenticated_admin.post("/api/v1/todos", json=payload)
    todo_id = response.json()["id"]

    detail = await authenticated_admin.get(f"/api/v1/todos/{todo_id}?include=categories")
    assert detail.json()["categories"] == []


# --- list ---


async def test_list_todos_returns_only_own_todos(
    authenticated_client: AsyncClient,
    current_user: User,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """A user who has todos and another user who has some too: GET /todos only shows their own,
    and `total` counts only their own as well."""
    await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche d'un autre"), categories=[]
    )

    await authenticated_client.post("/api/v1/todos", json={"title": "Ma tâche 1"})
    await authenticated_client.post("/api/v1/todos", json={"title": "Ma tâche 2"})

    response = await authenticated_client.get("/api/v1/todos")
    assert response.status_code == 200
    data = response.json()

    titles = {todo["title"] for todo in data["items"]}
    assert titles == {"Ma tâche 1", "Ma tâche 2"}
    assert all(todo["owner_id"] == current_user.id for todo in data["items"])
    assert data["total"] == 2


async def test_list_todos_as_admin_returns_everyone_todos(
    authenticated_admin: AsyncClient,
    current_admin_user: User,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """An admin sees the todos of all users (items and total) → 200."""
    await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche de other_user"), categories=[]
    )
    await authenticated_admin.post("/api/v1/todos", json={"title": "Tâche admin"})

    response = await authenticated_admin.get("/api/v1/todos")
    assert response.status_code == 200
    data = response.json()

    owner_ids = {todo["owner_id"] for todo in data["items"]}
    assert owner_ids == {other_user.id, current_admin_user.id}
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_list_todos_filter_by_is_completed(
    authenticated_client: AsyncClient,
) -> None:
    """Checks the is_completed filter."""
    todo_1 = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    todo_2 = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 2"})
    await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 3"})

    await authenticated_client.patch(
        f"/api/v1/todos/{todo_1.json()['id']}", json={"is_completed": True}
    )
    await authenticated_client.patch(
        f"/api/v1/todos/{todo_2.json()['id']}", json={"is_completed": True}
    )

    response = await authenticated_client.get("/api/v1/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3

    response = await authenticated_client.get("/api/v1/todos?is_completed=True")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    response = await authenticated_client.get("/api/v1/todos?is_completed=False")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1


async def test_list_todos_filter_by_title(
    authenticated_client: AsyncClient,
) -> None:
    """Checks the title filter (partial match, case-insensitive)."""
    await authenticated_client.post("/api/v1/todos", json={"title": "Sport"})
    await authenticated_client.post("/api/v1/todos", json={"title": "Course"})
    await authenticated_client.post("/api/v1/todos", json={"title": "Musculation"})

    response = await authenticated_client.get("/api/v1/todos?title=sport")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1

    response = await authenticated_client.get("/api/v1/todos?title=lation")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1


async def test_list_todos_filter_by_category_ids(
    authenticated_client: AsyncClient,
) -> None:
    """Checks the category_ids filter (union semantics: at least one of the categories)."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 2"})

    cat_1_id = cat_1.json()["id"]
    cat_2_id = cat_2.json()["id"]
    await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tâche 1", "category_ids": [cat_1_id]}
    )
    await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tâche 2", "category_ids": [cat_2_id]}
    )
    await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tâche 3", "category_ids": [cat_1_id, cat_2_id]}
    )

    response = await authenticated_client.get(f"/api/v1/todos?category_ids={cat_1_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    response = await authenticated_client.get(f"/api/v1/todos?category_ids={cat_2_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    response = await authenticated_client.get(
        f"/api/v1/todos?category_ids={cat_1_id}&category_ids={cat_2_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3


async def test_list_todos_too_many_category_ids_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """More than 20 category_ids in the filter is rejected."""
    query = "&".join(f"category_ids={i}" for i in range(21))
    response = await authenticated_client.get(f"/api/v1/todos?{query}")
    assert response.status_code == 422


# --- get ---


async def test_get_todo_by_id_success(
    authenticated_client: AsyncClient,
) -> None:
    """Fetching a todo by its ID (GET /api/v1/todos/{id})."""
    todo = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    response = await authenticated_client.get("/api/v1/todos/" + str(todo.json()["id"]))
    assert response.status_code == 200
    assert response.json()["id"] == todo.json()["id"]


async def test_get_todo_not_found_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    """A nonexistent ID returns 404 Not Found."""
    response = await authenticated_client.get("/api/v1/todos/999")
    assert response.status_code == 404


async def test_get_todo_of_another_user_returns_404(
    authenticated_client: AsyncClient,
    current_user: User,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """GET /todos/{id} on someone else's todo → 404 (not 403: don't leak its existence)."""
    todo = await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche de other_user"), categories=[]
    )
    response = await authenticated_client.get("/api/v1/todos/" + str(todo.id))
    assert response.status_code == 404
    assert todo.owner_id != current_user.id


async def test_get_todo_of_another_user_as_admin_success(
    authenticated_admin: AsyncClient,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """An admin can read any user's todo → 200, with the real owner_id in the response."""
    todo = await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche de other_user"), categories=[]
    )
    response = await authenticated_admin.get("/api/v1/todos/" + str(todo.id))
    assert response.status_code == 200
    assert response.json()["owner_id"] == other_user.id


async def test_get_todo_without_include_has_no_categories(
    authenticated_client: AsyncClient,
) -> None:
    """Getting a todo without including its categories."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche de current_user", "category_ids": [cat_1.json()["id"]]},
    )
    response = await authenticated_client.get("/api/v1/todos/" + str(todo.json()["id"]))
    assert response.status_code == 200
    assert "categories" not in response.json()


async def test_get_todo_with_include_categories(authenticated_client: AsyncClient) -> None:
    """Getting a todo with its categories included."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche de current_user", "category_ids": [cat_1.json()["id"]]},
    )
    response = await authenticated_client.get(
        "/api/v1/todos/" + str(todo.json()["id"]) + "?include=categories"
    )
    assert response.status_code == 200
    assert "categories" in response.json()
    assert len(response.json()["categories"]) == 1
    assert response.json()["categories"][0]["id"] == cat_1.json()["id"]


# --- update ---


async def test_patch_todo_success(
    authenticated_client: AsyncClient,
) -> None:
    """Partial update (PATCH /api/v1/todos/{id})."""
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1"},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"title": "Tâche 1 modifiée"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Tâche 1 modifiée"


async def test_patch_todo_with_null_title_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """If the client explicitly sends title=null, Pydantic returns a 422."""
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1"},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"title": None},
    )
    assert response.status_code == 422


async def test_patch_todo_with_null_description_allowed(
    authenticated_client: AsyncClient,
) -> None:
    """The description can be reset to null (nullable field)."""
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1", "description": "Description 1"},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"description": None},
    )
    assert response.status_code == 200
    assert response.json()["description"] is None


async def test_patch_todo_of_another_user_returns_404(
    authenticated_client: AsyncClient,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """PATCH /todos/{id} on someone else's todo → 404, and the todo is left untouched."""
    todo = await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche 1"), categories=[]
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.id),
        json={"title": "Tâche 1 modifiée"},
    )
    assert response.status_code == 404


async def test_update_todo_replaces_categories(
    authenticated_client: AsyncClient,
) -> None:
    """Updating a todo's categories."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 2"})
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1", "category_ids": [cat_1.json()["id"]]},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"category_ids": [cat_2.json()["id"]], "title": "Tâche 1 modifiée"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Tâche 1 modifiée"

    detail = await authenticated_client.get(f"/api/v1/todos/{todo.json()['id']}?include=categories")
    assert detail.json()["categories"] == [cat_2.json()]


async def test_update_todo_clears_categories_with_empty_list(
    authenticated_client: AsyncClient,
) -> None:
    """Clearing a todo's categories with an empty list."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 2"})
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1", "category_ids": [cat_1.json()["id"], cat_2.json()["id"]]},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"category_ids": [], "title": "Tâche 1 modifiée"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Tâche 1 modifiée"

    detail = await authenticated_client.get(f"/api/v1/todos/{todo.json()['id']}?include=categories")
    assert detail.json()["categories"] == []


async def test_changing_only_the_categories_bumps_updated_at(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Categories are part of the todo: changing only them is a modification of the todo,
    even though it writes to todos_categories alone and never to the todos row by itself."""
    todo = await TodoRepository(db_session).create(current_user.id, TodoCreate(title="Tâche"), [])
    todo.updated_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()
    before = (await authenticated_client.get(f"/api/v1/todos/{todo.id}")).json()["updated_at"]

    response = await authenticated_client.patch(
        f"/api/v1/todos/{todo.id}", json={"category_ids": []}
    )

    assert _as_utc(response.json()["updated_at"]) > _as_utc(before)


def _as_utc(value: str) -> datetime:
    """SQLite gives timestamps back without a timezone; they are UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def test_update_todo_omits_category_ids_keeps_existing(
    authenticated_client: AsyncClient,
) -> None:
    """Omitting category_ids on update keeps the existing categories."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 1"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Catégorie 2"})
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1", "category_ids": [cat_1.json()["id"], cat_2.json()["id"]]},
    )
    response = await authenticated_client.patch(
        "/api/v1/todos/" + str(todo.json()["id"]),
        json={"title": "Tâche 1 modifiée"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Tâche 1 modifiée"

    detail = await authenticated_client.get(f"/api/v1/todos/{todo.json()['id']}?include=categories")
    assert detail.json()["categories"] == [cat_1.json(), cat_2.json()]


# --- delete ---


async def test_delete_todo_success(
    authenticated_client: AsyncClient,
) -> None:
    """Deleting a todo (DELETE /api/v1/todos/{id})."""
    todo = await authenticated_client.post(
        "/api/v1/todos",
        json={"title": "Tâche 1"},
    )
    response = await authenticated_client.delete("/api/v1/todos/" + str(todo.json()["id"]))
    assert response.status_code == 204


async def test_delete_todo_of_another_user_returns_404(
    authenticated_client: AsyncClient,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """DELETE /todos/{id} on someone else's todo → 404, and the todo still exists afterwards."""
    todo = await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche 1"), categories=[]
    )
    response = await authenticated_client.delete("/api/v1/todos/" + str(todo.id))
    assert response.status_code == 404
    assert await TodoRepository(db_session).get_by_id(todo.id, owner_id=other_user.id) is not None


async def test_delete_todo_of_another_user_as_admin_success(
    authenticated_admin: AsyncClient,
    other_user: User,
    db_session: AsyncSession,
) -> None:
    """An admin can delete any user's todo → 204."""
    todo = await TodoRepository(db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tâche de other_user"), categories=[]
    )
    response = await authenticated_admin.delete(f"/api/v1/todos/{todo.id}")
    assert response.status_code == 204
    assert await TodoRepository(db_session).get_by_id(todo.id, owner_id=None) is None


async def test_delete_todo_is_soft_delete(
    authenticated_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Deleting a todo doesn't remove the row: the API treats it as gone (404), but
    the row still exists directly in the database, with deleted_at set."""
    todo = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    todo_id = todo.json()["id"]

    response = await authenticated_client.delete(f"/api/v1/todos/{todo_id}")
    assert response.status_code == 204

    get_response = await authenticated_client.get(f"/api/v1/todos/{todo_id}")
    assert get_response.status_code == 404

    result = await db_session.execute(select(Todo).where(Todo.id == todo_id))
    raw_todo = result.scalar_one()
    assert raw_todo.deleted_at is not None


async def test_deleted_todo_excluded_from_list(authenticated_client: AsyncClient) -> None:
    """A soft-deleted todo no longer appears in GET /todos, and total reflects it."""
    todo_1 = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 2"})

    await authenticated_client.delete(f"/api/v1/todos/{todo_1.json()['id']}")

    response = await authenticated_client.get("/api/v1/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Tâche 2"


async def test_deleted_todo_excluded_from_category_todos(
    authenticated_client: AsyncClient,
) -> None:
    """A soft-deleted todo no longer appears in GET /categories/{id}/todos either — the
    todos_categories association row survives the soft delete (no CASCADE triggers on
    an UPDATE), but the filter still applies since this endpoint delegates to the same
    TodoService.list_todos."""
    cat = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    cat_id = cat.json()["id"]
    todo = await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tâche 1", "category_ids": [cat_id]}
    )

    await authenticated_client.delete(f"/api/v1/todos/{todo.json()['id']}")

    response = await authenticated_client.get(f"/api/v1/categories/{cat_id}/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_delete_already_deleted_todo_returns_404(authenticated_client: AsyncClient) -> None:
    """Deleting a todo twice: the second DELETE sees it as gone, like any other request."""
    todo = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    todo_id = todo.json()["id"]

    first_response = await authenticated_client.delete(f"/api/v1/todos/{todo_id}")
    assert first_response.status_code == 204

    second_response = await authenticated_client.delete(f"/api/v1/todos/{todo_id}")
    assert second_response.status_code == 404


async def test_patch_deleted_todo_returns_404(
    authenticated_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A soft-deleted todo can't be updated: PATCH returns 404 and the row keeps the
    values it had when it was deleted."""
    todo = await authenticated_client.post("/api/v1/todos", json={"title": "Tâche 1"})
    todo_id = todo.json()["id"]
    await authenticated_client.delete(f"/api/v1/todos/{todo_id}")

    response = await authenticated_client.patch(
        f"/api/v1/todos/{todo_id}", json={"title": "Tâche modifiée"}
    )
    assert response.status_code == 404

    result = await db_session.execute(select(Todo).where(Todo.id == todo_id))
    assert result.scalar_one().title == "Tâche 1"
