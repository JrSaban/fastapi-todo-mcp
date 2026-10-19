from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.modules.categories.models import Category
from src.modules.categories.schemas import CategoryCreate, CategoryUpdate
from src.modules.categories.service import CategoryService
from src.modules.users.models import User

# --- list_categories ---


async def test_list_categories_falls_back_to_db_when_redis_get_fails():
    """A RedisError on the cache read doesn't crash list_categories — it falls back
    to the repository, the same as a genuine cache miss."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    # 1er appel = lecture de "categories:list:gen", 2e = lecture de la clé de liste elle-même
    mock_redis_client.get.side_effect = ["0", RedisError("boom")]
    mock_category_repo.get_all.return_value = [
        Category(id=1, name="Sport", color="#FAFAFA", created_by_id=1, created_at=datetime.now())
    ]
    mock_category_repo.count.return_value = 1

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    categories, total = await service.list_categories()

    assert total == 1
    assert categories[0].name == "Sport"
    mock_category_repo.get_all.assert_called_once_with(skip=0, limit=25, name=None)


async def test_list_categories_falls_back_to_db_when_redis_set_fails():
    """A RedisError on the cache write after a DB fetch still returns the fresh data;
    it just doesn't get cached."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_redis_client.get.return_value = None
    mock_redis_client.set.side_effect = RedisError("boom")
    mock_category_repo.get_all.return_value = [
        Category(id=1, name="Sport", color="#FAFAFA", created_by_id=1, created_at=datetime.now())
    ]
    mock_category_repo.count.return_value = 1

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    categories, total = await service.list_categories()

    assert total == 1
    assert categories[0].name == "Sport"


async def test_list_categories_ignores_corrupted_cache_entry():
    """Malformed JSON (or a payload that no longer matches CategoryResponse) stored
    under the list cache key doesn't crash the request — it's treated like a cache miss."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_redis_client.get.side_effect = ["0", "not valid json"]
    mock_category_repo.get_all.return_value = [
        Category(id=1, name="Sport", color="#FAFAFA", created_by_id=1, created_at=datetime.now())
    ]
    mock_category_repo.count.return_value = 1

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    categories, total = await service.list_categories()

    assert total == 1
    assert categories[0].name == "Sport"


# --- get_category_cached ---


async def test_get_category_cached_falls_back_to_db_when_redis_unavailable():
    """A RedisError on get_category_cached's Redis calls doesn't prevent the category
    from being returned via the repository."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_redis_client.get.side_effect = RedisError("boom")
    fake_category = Category(
        id=1, name="Sport", color="#FAFAFA", created_by_id=1, created_at=datetime.now()
    )
    mock_category_repo.get_by_id.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    result = await service.get_category_cached(1)

    assert result.name == "Sport"
    mock_category_repo.get_by_id.assert_called_once_with(1)


# --- get_category_or_404 ---


async def test_get_category_or_404_raises_when_not_found():
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.get_by_id.return_value = None

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    with pytest.raises(NotFoundError):
        await service.get_category_or_404(1)

    mock_category_repo.get_by_id.assert_called_once_with(1)


async def test_get_category_or_404_returns_category_when_found():
    fake_category = Category(id=1, name="Test")
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.get_by_id.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    result = await service.get_category_or_404(1)

    assert result == fake_category
    mock_category_repo.get_by_id.assert_called_once_with(1)


# --- create_category ---


async def test_create_category(current_user: User):
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    fake_category = Category(id=1, name="Test")
    mock_category_repo.create.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryCreate(name="Test")

    result = await service.create_category(created_by_id=current_user.id, data=data)

    assert result == fake_category
    mock_category_repo.create.assert_called_once_with(created_by_id=current_user.id, data=data)


async def test_create_duplicate_category_raises_conflict_error(current_user: User):
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.create.side_effect = IntegrityError("stmt", {}, Exception("orig"))

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryCreate(name="Sport")

    with pytest.raises(ConflictError):
        await service.create_category(created_by_id=current_user.id, data=data)


# --- update_category ---


async def test_update_category_successfully(current_user: User):
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    fake_category = Category(id=1, created_by_id=current_user.id, name="Sport")
    fake_category_updated = Category(id=1, created_by_id=current_user.id, name="House")

    mock_category_repo.get_by_id.return_value = fake_category
    mock_category_repo.update.return_value = fake_category_updated

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="House")

    result = await service.update_category(user=current_user, entity_id=1, data=data)

    assert result == fake_category_updated
    mock_category_repo.get_by_id.assert_called_once_with(1)
    mock_category_repo.update.assert_called_once_with(fake_category, data)


async def test_update_category_with_duplicate_name_raises_conflict_error(current_user: User):
    fake_category = Category(id=2, created_by_id=current_user.id, name="House")
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.get_by_id.return_value = fake_category
    mock_category_repo.update.side_effect = IntegrityError("stmt", {}, Exception("orig"))

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="Sport")

    with pytest.raises(ConflictError):
        await service.update_category(user=current_user, entity_id=2, data=data)

    mock_category_repo.get_by_id.assert_called_once_with(2)


async def test_update_category_by_non_owner_non_admin_raises_forbidden_error(current_user: User):
    fake_category = Category(id=1, created_by_id=current_user.id + 1, name="Sport")
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.get_by_id.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="House")

    with pytest.raises(ForbiddenError):
        await service.update_category(user=current_user, entity_id=1, data=data)

    mock_category_repo.get_by_id.assert_called_once_with(1)
    mock_category_repo.update.assert_not_called()


async def test_update_category_by_admin_on_others_category_success(current_admin_user: User):
    fake_category = Category(id=1, created_by_id=current_admin_user.id + 1, name="Sport")
    fake_category_updated = Category(id=1, created_by_id=current_admin_user.id + 1, name="House")
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_category_repo.get_by_id.return_value = fake_category
    mock_category_repo.update.return_value = fake_category_updated

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="House")

    result = await service.update_category(user=current_admin_user, entity_id=1, data=data)

    assert result == fake_category_updated
    mock_category_repo.update.assert_called_once_with(fake_category, data)


async def test_update_category_invalidates_item_cache(current_user: User):
    """update_category calls _invalidate_cache with the entity's own id — a regression
    test for the bug where a positional call bound entity_id to the wrong parameter
    and silently stopped invalidating the item cache."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    fake_category = Category(id=1, created_by_id=current_user.id, name="Sport")
    fake_category_updated = Category(id=1, created_by_id=current_user.id, name="House")
    mock_category_repo.get_by_id.return_value = fake_category
    mock_category_repo.update.return_value = fake_category_updated

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="House")

    await service.update_category(user=current_user, entity_id=1, data=data)

    mock_redis_client.incr.assert_called_once_with("categories:list:gen")
    mock_redis_client.delete.assert_called_once_with("category:1")


async def test_invalidate_cache_does_not_raise_when_redis_unavailable(current_user: User):
    """update_category/delete_category still succeed even if the cache invalidation
    step (INCR / DELETE) raises a RedisError."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    mock_redis_client.incr.side_effect = RedisError("boom")
    fake_category = Category(id=1, created_by_id=current_user.id, name="Sport")
    fake_category_updated = Category(id=1, created_by_id=current_user.id, name="House")
    mock_category_repo.get_by_id.return_value = fake_category
    mock_category_repo.update.return_value = fake_category_updated

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )
    data = CategoryUpdate(name="House")

    result = await service.update_category(user=current_user, entity_id=1, data=data)

    assert result == fake_category_updated


# --- delete_category ---


async def test_delete_category_by_admin_calls_repo_delete_when_found(current_admin_user: User):
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    fake_category = Category(id=1, created_by_id=current_admin_user.id, name="Test")
    mock_category_repo.get_by_id.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    await service.delete_category(user=current_admin_user, entity_id=1)

    mock_category_repo.get_by_id.assert_called_once_with(1)
    mock_category_repo.delete.assert_called_once_with(fake_category)


async def test_delete_category_by_non_admin_raises_forbidden_error(current_user: User):
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    with pytest.raises(ForbiddenError):
        await service.delete_category(user=current_user, entity_id=1)

    mock_category_repo.get_by_id.assert_not_called()
    mock_category_repo.delete.assert_not_called()


async def test_delete_category_invalidates_item_cache(current_admin_user: User):
    """Same regression test as above, for delete_category."""
    mock_category_repo = AsyncMock()
    mock_redis_client = AsyncMock()
    fake_category = Category(id=1, created_by_id=current_admin_user.id, name="Sport")
    mock_category_repo.get_by_id.return_value = fake_category

    service = CategoryService(
        repository=mock_category_repo, redis_client=mock_redis_client, audit_service=AsyncMock()
    )

    await service.delete_category(user=current_admin_user, entity_id=1)

    mock_redis_client.incr.assert_called_once_with("categories:list:gen")
    mock_redis_client.delete.assert_called_once_with("category:1")
