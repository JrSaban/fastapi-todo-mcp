"""Tests d'intégration des endpoints Categories."""

import asyncio
import json
from unittest.mock import AsyncMock

import fakeredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import hash_password
from src.modules.categories.models import Category
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate, CategoryUpdate
from src.modules.categories.service import CategoryService
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate
from src.modules.users.models import User
from src.modules.users.repository import UserRepository
from src.modules.users.schemas import UserCreate
from tests.conftest import test_session_factory as session_factory

# --- create ---


async def test_create_category_success(authenticated_client: AsyncClient) -> None:
    """Successful category creation (POST /api/v1/categories)."""
    payload = {
        "name": "Sport",
    }
    response = await authenticated_client.post("/api/v1/categories", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["name"] == payload["name"]
    assert data["color"] == "#FAFAFA"
    assert "created_at" in data


async def test_create_category_missing_name_returns_422(authenticated_client: AsyncClient) -> None:
    """Omitting the name is rejected by Pydantic (422 Unprocessable Entity)."""
    response = await authenticated_client.post("/api/v1/categories", json={"color": "#FF0000"})
    assert response.status_code == 422


async def test_create_category_empty_name_returns_422(authenticated_client: AsyncClient) -> None:
    """An empty name ("") is rejected thanks to min_length=1."""
    response = await authenticated_client.post(
        "/api/v1/categories", json={"name": "", "color": "#FF0000"}
    )
    assert response.status_code == 422


async def test_create_category_empty_color_returns_422(authenticated_client: AsyncClient) -> None:
    """An empty color ("") is rejected thanks to the regex pattern."""
    response = await authenticated_client.post(
        "/api/v1/categories", json={"name": "Test", "color": ""}
    )
    assert response.status_code == 422


async def test_create_category_uppercases_color(authenticated_client: AsyncClient) -> None:
    """The color field is uppercased."""
    create_res = await authenticated_client.post(
        "/api/v1/categories", json={"name": "Sport", "color": "#fbfbfb"}
    )
    category_id = create_res.json()["id"]

    category = await authenticated_client.get(f"/api/v1/categories/{category_id}")
    assert category.status_code == 200
    assert category.json()["id"] == category_id
    assert category.json()["name"] == "Sport"
    assert category.json()["color"] == "#FBFBFB"


async def test_create_category_duplicate_name_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """A category with the same name is rejected (409 Conflict)."""
    # 1. Création de la catégorie
    await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})

    # 2. Tentative de création de la même catégorie
    response = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Une catégorie avec le nom Sport existe déjà."


async def test_create_category_case_insensitive_duplicate_returns_409(
    authenticated_client: AsyncClient,
) -> None:
    """A duplicate name in uppercase is also rejected (409 Conflict)."""
    # 1. Création de la catégorie en minuscules
    await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})

    # 2. Tentative de création de la même catégorie en majuscules
    response = await authenticated_client.post("/api/v1/categories", json={"name": "SPORT"})
    assert response.status_code == 409
    assert response.json()["detail"] == "Une catégorie avec le nom SPORT existe déjà."


async def test_create_category_concurrent_same_name_no_duplicate(
    redis_client: fakeredis.FakeAsyncRedis,
) -> None:
    """Two concurrent requests creating a category with the same name must not both
    succeed. The database's unique constraint on `Category.name` is the real guard,
    and `create_category` must translate the resulting IntegrityError into a clean
    ConflictError instead of leaking it."""

    # A real, committed creator: both concurrent sessions must see it, and the
    # foreign key on created_by_id is enforced.
    async with session_factory() as setup_session:
        creator = await UserRepository(setup_session).create(
            UserCreate(
                email="creator@test.com",
                first_name="Category",
                last_name="Creator",
                password="secret123",
                confirm_password="secret123",
            ),
            hash_password("secret123"),
        )
        await setup_session.commit()

    write_lock = asyncio.Lock()

    async def attempt(name: str) -> Category | Exception:
        async with session_factory() as session:
            repository = CategoryRepository(session)
            original_create = repository.create

            async def create_serialized(*args, **kwargs) -> Category:
                async with write_lock:
                    return await original_create(*args, **kwargs)

            repository.create = create_serialized

            # No real audit write: its extra flush would let the losing session roll back the
            # single shared test connection before the winner commits (a StaticPool artifact).
            service = CategoryService(repository, redis_client, AsyncMock())
            try:
                category = await service.create_category(
                    created_by_id=creator.id, data=CategoryCreate(name=name)
                )
                await session.commit()
                return category
            except Exception as exc:
                await session.rollback()
                return exc

    results = await asyncio.gather(attempt("Sport"), attempt("sport"))

    # Whichever attempt loses the race must never surface a raw IntegrityError
    raw_integrity_errors = [r for r in results if isinstance(r, IntegrityError)]
    assert not raw_integrity_errors, (
        f"IntegrityError leaked out of create_category instead of being "
        f"translated into ConflictError: {raw_integrity_errors}"
    )

    # Regardless of which attempt "wins", only one row must actually exist —
    # checked independently, not by trusting either attempt's return value.
    async with session_factory() as verify_session:
        count = await CategoryRepository(verify_session).count(name="Sport")
    assert count == 1


async def test_create_category_reuses_name_of_deleted_category(
    authenticated_admin: AsyncClient,
) -> None:
    """The unique index on name is partial (only among non-deleted rows): once a
    category is soft-deleted, its name becomes free to reuse — unlike a plain
    unique index, which would keep blocking it forever."""
    create_res = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]

    del_res = await authenticated_admin.delete(f"/api/v1/categories/{category_id}")
    assert del_res.status_code == 204

    response = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    assert response.status_code == 201
    assert response.json()["id"] != category_id


async def test_create_category_invalidates_list_cache(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """Creating a category bumps categories:list:gen, so a subsequent GET /categories
    with the same filters no longer hits the previously cached (now stale) entry."""
    await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    gen = await redis_client.get("categories:list:gen")
    assert gen == "1"

    await authenticated_client.post("/api/v1/categories", json={"name": "House"})
    gen = await redis_client.get("categories:list:gen")
    assert gen == "2"


# --- list ---


async def test_list_categories(authenticated_client: AsyncClient) -> None:
    """Fetching the list of categories (GET /api/v1/categories)."""
    # 1. Création de deux catégories
    await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    await authenticated_client.post(
        "/api/v1/categories", json={"name": "House", "color": "#00FF00"}
    )

    # 2. Récupération de la liste
    response = await authenticated_client.get("/api/v1/categories")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0]["name"] == "House"  # Tri croissant par nom


async def test_list_categories_filter_by_name(authenticated_client: AsyncClient) -> None:
    """Checks filtering categories by name."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    cat_2 = await authenticated_client.post("/api/v1/categories", json={"name": "Portable"})
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})

    response = await authenticated_client.get("/api/v1/categories?name=port")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0] == cat_2.json()
    assert data["items"][1] == cat_1.json()


async def test_list_categories_filter_by_name_escapes_wildcards(
    authenticated_client: AsyncClient,
) -> None:
    """A literal % or _ in the search term is matched literally, not as a SQL
    wildcard: "50%" doesn't also match "50X off", and "a_b" doesn't match "aXb"."""
    percent_cat = await authenticated_client.post("/api/v1/categories", json={"name": "50% off"})
    await authenticated_client.post("/api/v1/categories", json={"name": "50X off"})
    underscore_cat = await authenticated_client.post("/api/v1/categories", json={"name": "a_b"})
    await authenticated_client.post("/api/v1/categories", json={"name": "aXb"})

    response = await authenticated_client.get("/api/v1/categories", params={"name": "50%"})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0] == percent_cat.json()

    response = await authenticated_client.get("/api/v1/categories", params={"name": "a_b"})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0] == underscore_cat.json()


async def test_list_categories_with_name_filter_is_never_cached(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """A name-filtered search is never cached at all — not stored, not read from,
    no matter how many times it's repeated."""
    await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})

    await authenticated_client.get("/api/v1/categories?name=port")
    await authenticated_client.get("/api/v1/categories?name=port")

    keys = await redis_client.keys("categories:list:*")
    assert keys == ["categories:list:gen"]


async def test_list_categories_skip_too_large_returns_422(
    authenticated_client: AsyncClient,
) -> None:
    """skip beyond the upper bound (2000) is rejected, so a client can't generate
    unlimited cache keys by varying it."""
    response = await authenticated_client.get("/api/v1/categories?skip=2001")
    assert response.status_code == 422


async def test_list_categories_without_token_returns_401(client: AsyncClient) -> None:
    """GET /categories with no Authorization header at all → 401."""
    response = await client.get("/api/v1/categories")
    assert response.status_code == 401


async def test_list_categories_stores_it_in_cache(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The first GET /categories stores the result under a categories:list:* key."""
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})
    gen = await redis_client.get("categories:list:gen")
    assert gen == "1"
    entires = await redis_client.keys(f"categories:list:{gen}:*")
    assert len(entires) == 0
    await authenticated_client.get("/api/v1/categories")
    entires = await redis_client.keys(f"categories:list:{gen}:*")
    assert len(entires) == 1


async def test_list_categories_is_served_from_cache(
    authenticated_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Once cached, GET /categories doesn't re-read the database: a category created
    directly via the repository (bypassing the service, so no cache invalidation)
    isn't reflected in a second call with the same filters."""
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})
    response = await authenticated_client.get("/api/v1/categories")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "House"

    repository = CategoryRepository(db_session)
    await repository.create(created_by_id=1, data=CategoryCreate(name="Sport"))

    response = await authenticated_client.get("/api/v1/categories")
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "House"


async def test_list_categories_different_filters_use_different_cache_entries(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """Two GET /categories calls with different skip/limit produce two distinct
    categories:list:* entries, not one overwriting the other. (name isn't part of
    this: a name-filtered search is never cached at all — see the dedicated test.)"""
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})

    await authenticated_client.get("/api/v1/categories?limit=10")
    await authenticated_client.get("/api/v1/categories?limit=25")

    gen = await redis_client.get("categories:list:gen")
    assert gen == "1"
    keys = await redis_client.keys(f"categories:list:{gen}:*")
    assert len(keys) == 2


async def test_deleted_category_excluded_from_list(authenticated_admin: AsyncClient) -> None:
    """A soft-deleted category no longer appears in GET /categories, and total reflects it."""
    cat_1 = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    await authenticated_admin.post("/api/v1/categories", json={"name": "House"})

    await authenticated_admin.delete(f"/api/v1/categories/{cat_1.json()['id']}")

    response = await authenticated_admin.get("/api/v1/categories")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "House"


# --- get ---


async def test_get_category_by_id_success(authenticated_client: AsyncClient) -> None:
    """Fetching a category by its ID (GET /api/v1/categories/{id})."""
    create_res = await authenticated_client.post(
        "/api/v1/categories", json={"name": "Sport", "color": "#FF0000"}
    )
    created_id = create_res.json()["id"]

    response = await authenticated_client.get(f"/api/v1/categories/{created_id}")
    assert response.status_code == 200
    assert response.json()["id"] == created_id
    assert response.json()["name"] == "Sport"
    assert response.json()["color"] == "#FF0000"


async def test_get_category_not_found_returns_404(authenticated_client: AsyncClient) -> None:
    """A nonexistent ID returns 404 Not Found."""
    response = await authenticated_client.get("/api/v1/categories/99999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Catégorie avec l'ID 99999 introuvable."


async def test_get_category_without_token_returns_401(client: AsyncClient) -> None:
    """GET /categories/{id} with no Authorization header at all → 401."""
    response = await client.get("/api/v1/categories/1")
    assert response.status_code == 401


async def test_get_category_stores_it_in_cache(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """The first GET /categories/{id} stores the category in Redis."""
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]
    assert await redis_client.get(f"category:{category_id}") is None

    response = await authenticated_client.get(f"/api/v1/categories/{category_id}")
    assert response.status_code == 200

    cached = await redis_client.get(f"category:{category_id}")
    assert cached is not None
    assert json.loads(cached) == response.json()


async def test_get_category_is_served_from_cache(
    authenticated_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Once cached, GET /categories/{id} no longer reads the database: a change made
    directly in the database (bypassing the service, so no invalidation) isn't seen."""
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]
    await authenticated_client.get(f"/api/v1/categories/{category_id}")

    # Modification directe en base, sans passer par le service
    repository = CategoryRepository(db_session)
    category = await repository.get_by_id(category_id)
    assert category is not None
    await repository.update(category, CategoryUpdate(name="House"))

    response = await authenticated_client.get(f"/api/v1/categories/{category_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Sport"


# --- update ---


async def test_patch_category_success(authenticated_client: AsyncClient) -> None:
    """Partial update by its creator (PATCH /api/v1/categories/{id})."""
    create_res = await authenticated_client.post(
        "/api/v1/categories", json={"name": "Sport", "color": "#FF0000"}
    )
    category_id = create_res.json()["id"]

    # On ne modifie QUE name
    update_res = await authenticated_client.patch(
        f"/api/v1/categories/{category_id}", json={"name": "House"}
    )
    assert update_res.status_code == 200
    data = update_res.json()
    assert data["name"] == "House"
    assert data["color"] == "#FF0000"  # La couleur n'a pas été effacée !


async def test_patch_category_by_admin_success(
    authenticated_admin: AsyncClient, other_user: User, db_session: AsyncSession
) -> None:
    """An admin can partially update a category they didn't create."""
    cat_1 = await CategoryRepository(db_session).create(
        created_by_id=other_user.id, data=CategoryCreate(name="Sport")
    )

    update_res = await authenticated_admin.patch(
        f"/api/v1/categories/{cat_1.id}", json={"name": "House"}
    )
    assert update_res.status_code == 200
    data = update_res.json()
    assert data["name"] == "House"


async def test_patch_category_by_non_admin_not_owner_returns_403(
    authenticated_client: AsyncClient, other_user: User, db_session: AsyncSession
) -> None:
    """A non-admin, non-owner user updating a category → 403."""
    cat_1 = await CategoryRepository(db_session).create(
        created_by_id=other_user.id, data=CategoryCreate(name="Sport")
    )

    update_res = await authenticated_client.patch(
        f"/api/v1/categories/{cat_1.id}", json={"name": "House"}
    )
    assert update_res.status_code == 403


async def test_patch_category_with_null_name_returns_422(authenticated_client: AsyncClient) -> None:
    """If the client explicitly sends name=null, Pydantic returns a 422."""
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]

    response = await authenticated_client.patch(
        f"/api/v1/categories/{category_id}", json={"name": None}
    )
    assert response.status_code == 422


async def test_update_category_name_conflict_returns_409(authenticated_client: AsyncClient) -> None:
    """Renaming a category to another existing category's name returns 409."""
    # 1. Création de deux catégories
    await authenticated_client.post("/api/v1/categories", json={"name": "House"})
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]

    # 2. Tentative de renommer la catégorie "Sport" en "house"
    response = await authenticated_client.patch(
        f"/api/v1/categories/{category_id}", json={"name": "house"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Une catégorie avec le nom house existe déjà."


async def test_update_category_reuses_name_of_deleted_category(
    authenticated_admin: AsyncClient,
) -> None:
    """Renaming a category to the name of a soft-deleted one succeeds — the partial
    unique index doesn't count deleted rows, whether the write is an INSERT or an
    UPDATE."""
    deleted_cat = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    await authenticated_admin.delete(f"/api/v1/categories/{deleted_cat.json()['id']}")

    create_res = await authenticated_admin.post("/api/v1/categories", json={"name": "House"})
    category_id = create_res.json()["id"]

    response = await authenticated_admin.patch(
        f"/api/v1/categories/{category_id}", json={"name": "Sport"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Sport"


async def test_update_category_keep_same_name_succeeds(authenticated_client: AsyncClient) -> None:
    """Renaming a category to its own current name doesn't trigger a 409 Conflict."""
    # 1. Création de la catégorie
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "sport"})
    category_id = create_res.json()["id"]

    # 2. Tentative de renommer la catégorie "sport" en "Sport"
    response = await authenticated_client.patch(
        f"/api/v1/categories/{category_id}", json={"name": "Sport"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == category_id
    assert response.json()["name"] == "Sport"


async def test_patch_category_invalidates_cache(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """PATCH /categories/{id} removes the cached entry, so the next GET returns fresh data."""
    create_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]
    await authenticated_client.get(f"/api/v1/categories/{category_id}")

    await authenticated_client.patch(f"/api/v1/categories/{category_id}", json={"name": "House"})
    assert await redis_client.get(f"category:{category_id}") is None

    response = await authenticated_client.get(f"/api/v1/categories/{category_id}")
    assert response.json()["name"] == "House"


async def test_patch_category_invalidates_list_cache(
    authenticated_client: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """Updating a category bumps categories:list:gen, so a previously cached list
    reflects the change on the next GET."""
    created_res = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    gen = await redis_client.get("categories:list:gen")
    assert gen == "1"

    response = await authenticated_client.get("/api/v1/categories")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Sport"

    await authenticated_client.patch(
        f"/api/v1/categories/{created_res.json()['id']}", json={"name": "House"}
    )
    gen = await redis_client.get("categories:list:gen")
    assert gen == "2"

    response = await authenticated_client.get("/api/v1/categories")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "House"


# --- delete ---


async def test_delete_category_by_admin_success(
    authenticated_admin: AsyncClient, other_user: User, db_session: AsyncSession
) -> None:
    """An admin can delete a category they didn't create (DELETE /api/v1/categories/{id})."""
    cat_1 = await CategoryRepository(db_session).create(
        created_by_id=other_user.id, data=CategoryCreate(name="Sport")
    )

    # 1. Suppression
    del_res = await authenticated_admin.delete(f"/api/v1/categories/{cat_1.id}")
    assert del_res.status_code == 204

    # 2. Vérification que la catégorie n'existe plus
    get_res = await authenticated_admin.get(f"/api/v1/categories/{cat_1.id}")
    assert get_res.status_code == 404


async def test_delete_category_by_non_admin_returns_403(authenticated_client: AsyncClient) -> None:
    """A non-admin user deleting a category (even their own) → 403."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})

    del_res = await authenticated_client.delete(f"/api/v1/categories/{cat_1.json()['id']}")
    assert del_res.status_code == 403


async def test_delete_category_removes_association_but_keeps_todo(
    authenticated_admin: AsyncClient,
) -> None:
    """Deleting a category attached to a todo only removes the association;
    the todo itself survives, just without that category."""
    cat = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    cat_id = cat.json()["id"]
    todo = await authenticated_admin.post(
        "/api/v1/todos", json={"title": "Tache", "category_ids": [cat_id]}
    )
    todo_id = todo.json()["id"]

    del_res = await authenticated_admin.delete(f"/api/v1/categories/{cat_id}")
    assert del_res.status_code == 204

    get_todo = await authenticated_admin.get(f"/api/v1/todos/{todo_id}?include=categories")
    assert get_todo.status_code == 200
    assert get_todo.json()["categories"] == []


async def test_delete_category_is_soft_delete(
    authenticated_admin: AsyncClient, db_session: AsyncSession
) -> None:
    """Deleting a category doesn't remove the row: the API treats it as gone (404),
    but the row still exists directly in the database, with deleted_at set."""
    create_res = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]

    response = await authenticated_admin.delete(f"/api/v1/categories/{category_id}")
    assert response.status_code == 204

    get_response = await authenticated_admin.get(f"/api/v1/categories/{category_id}")
    assert get_response.status_code == 404

    result = await db_session.execute(select(Category).where(Category.id == category_id))
    raw_category = result.scalar_one()
    assert raw_category.deleted_at is not None


async def test_delete_category_invalidates_cache(
    authenticated_admin: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """DELETE /categories/{id} removes the cached entry, so the next GET returns 404."""
    create_res = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    category_id = create_res.json()["id"]
    await authenticated_admin.get(f"/api/v1/categories/{category_id}")

    del_res = await authenticated_admin.delete(f"/api/v1/categories/{category_id}")
    assert del_res.status_code == 204
    assert await redis_client.get(f"category:{category_id}") is None

    get_res = await authenticated_admin.get(f"/api/v1/categories/{category_id}")
    assert get_res.status_code == 404


async def test_delete_category_invalidates_list_cache(
    authenticated_admin: AsyncClient, redis_client: fakeredis.FakeAsyncRedis
) -> None:
    """Deleting a category bumps categories:list:gen, so a previously cached list
    no longer includes it on the next GET."""
    created_res = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    await authenticated_admin.post("/api/v1/categories", json={"name": "House"})
    gen = await redis_client.get("categories:list:gen")
    assert gen == "2"

    response = await authenticated_admin.get("/api/v1/categories")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2

    await authenticated_admin.delete(f"/api/v1/categories/{created_res.json()['id']}")
    gen = await redis_client.get("categories:list:gen")
    assert gen == "3"

    response = await authenticated_admin.get("/api/v1/categories")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["name"] == "House"


# --- category todos ---


async def test_get_todos_by_category(
    authenticated_client: AsyncClient, other_user: User, db_session: AsyncSession
) -> None:
    """Fetching the todos associated with a category."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    cat_1_id = cat_1.json()["id"]
    category = await CategoryRepository(session=db_session).get_by_id(cat_1_id)
    assert category is not None

    await TodoRepository(session=db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tache other user"), categories=[category]
    )

    todo_1 = await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tache 1", "category_ids": [cat_1_id]}
    )
    todo_2 = await authenticated_client.post(
        "/api/v1/todos", json={"title": "Tache 2", "category_ids": [cat_1_id]}
    )

    # 2. Récupération des tâches de la catégorie
    response = await authenticated_client.get(f"/api/v1/categories/{cat_1_id}/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0]["id"] == todo_2.json()["id"]
    assert data["items"][1]["id"] == todo_1.json()["id"]


async def test_get_todos_by_category_as_admin(
    authenticated_admin: AsyncClient, other_user: User, db_session: AsyncSession
) -> None:
    """An admin fetching a category's todos sees every user's todos."""
    cat_1 = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    cat_1_id = cat_1.json()["id"]
    category = await CategoryRepository(session=db_session).get_by_id(cat_1_id)
    assert category is not None

    todo_0 = await TodoRepository(session=db_session).create(
        owner_id=other_user.id, data=TodoCreate(title="Tache other user"), categories=[category]
    )

    todo_1 = await authenticated_admin.post(
        "/api/v1/todos", json={"title": "Tache 1", "category_ids": [cat_1_id]}
    )
    todo_2 = await authenticated_admin.post(
        "/api/v1/todos", json={"title": "Tache 2", "category_ids": [cat_1_id]}
    )

    # 2. Récupération des tâches de la catégorie
    response = await authenticated_admin.get(f"/api/v1/categories/{cat_1_id}/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["items"][0]["id"] == todo_2.json()["id"]
    assert data["items"][1]["id"] == todo_1.json()["id"]
    assert data["items"][2]["id"] == todo_0.id


async def test_get_todos_by_category_empty(authenticated_client: AsyncClient) -> None:
    """An empty list is returned when a category has no todos."""
    cat_1 = await authenticated_client.post("/api/v1/categories", json={"name": "Sport"})
    cat_1_id = cat_1.json()["id"]

    response = await authenticated_client.get(f"/api/v1/categories/{cat_1_id}/todos")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_get_todos_by_deleted_category_returns_404(
    authenticated_admin: AsyncClient,
) -> None:
    """GET /categories/{id}/todos on a soft-deleted category → 404, same as any other
    missing category — get_category_or_404 already filters deleted_at IS NULL."""
    cat_1 = await authenticated_admin.post("/api/v1/categories", json={"name": "Sport"})
    cat_1_id = cat_1.json()["id"]
    await authenticated_admin.delete(f"/api/v1/categories/{cat_1_id}")

    response = await authenticated_admin.get(f"/api/v1/categories/{cat_1_id}/todos")
    assert response.status_code == 404
