from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import NotFoundError
from src.modules.todos.models import Todo
from src.modules.todos.schemas import TodoCreate, TodoUpdate
from src.modules.todos.service import TodoService
from src.modules.users.models import User


@pytest.fixture
def regular_user():
    return User(id=1, email="user@example.com", hashed_password="test", is_admin=False)


@pytest.fixture
def admin_user():
    return User(id=2, email="admin@example.com", hashed_password="test", is_admin=True)


# --- list_todos ---


@pytest.mark.parametrize(
    ("is_admin", "expected_owner_id"),
    [
        (False, 1),  # regular user → filtered on their own id
        (True, None),  # admin → no owner filter
    ],
)
async def test_list_todos_owner_filter_depends_on_role(is_admin, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    fake_todos = [Todo(id=1, owner_id=1, title="Test")]
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_all.return_value = fake_todos
    mock_todo_repo.count.return_value = 1

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    todos, total = await service.list_todos(
        current_user=user,
        skip=5,
        limit=10,
        title="Te",
        is_completed=True,
        category_ids=[1, 2],
    )

    assert todos == fake_todos
    assert total == 1
    mock_todo_repo.get_all.assert_called_once_with(
        skip=5,
        limit=10,
        owner_id=expected_owner_id,
        title="Te",
        is_completed=True,
        category_ids=[1, 2],
    )
    mock_todo_repo.count.assert_called_once_with(
        owner_id=expected_owner_id, title="Te", is_completed=True, category_ids=[1, 2]
    )


@pytest.mark.parametrize(
    ("is_admin", "expected_owner_id"),
    [
        (False, 1),  # regular user → the requested owner_id is ignored, still their own id
        (True, 5),  # admin → the requested owner_id is applied
    ],
)
async def test_list_todos_owner_id_filter_only_applies_to_admins(is_admin, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_all.return_value = []
    mock_todo_repo.count.return_value = 0

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    await service.list_todos(current_user=user, owner_id=5)

    assert mock_todo_repo.get_all.call_args.kwargs["owner_id"] == expected_owner_id
    assert mock_todo_repo.count.call_args.kwargs["owner_id"] == expected_owner_id


# --- get_todo_or_404 ---


async def test_get_todo_or_404_returns_todo_when_found(regular_user: User):
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    result = await service.get_todo_or_404(current_user=regular_user, entity_id=1)

    assert result == fake_todo
    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=False)


async def test_get_todo_or_404_raises_when_not_found(regular_user: User):
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = None

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    with pytest.raises(NotFoundError):
        await service.get_todo_or_404(current_user=regular_user, entity_id=1)

    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=False)


async def test_get_todo_by_another_user_raises_not_found_error(regular_user: User):
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = None
    regular_user.id = 2

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    with pytest.raises(NotFoundError):
        await service.get_todo_or_404(current_user=regular_user, entity_id=1)

    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=2, entity_id=1, with_categories=False)


async def test_get_todo_by_admin_returns_todo(admin_user: User):
    mock_todo_repo = AsyncMock()
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo.get_by_id.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    result = await service.get_todo_or_404(current_user=admin_user, entity_id=1)

    assert result == fake_todo
    assert result.owner_id != admin_user.id
    mock_todo_repo.get_by_id.assert_called_once_with(
        owner_id=None, entity_id=1, with_categories=False
    )


async def test_get_todo_or_404_forwards_with_categories(regular_user):
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    result = await service.get_todo_or_404(
        current_user=regular_user, entity_id=1, with_categories=True
    )

    assert result == fake_todo
    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=True)


# --- create_todo ---


async def test_create_todo_resolves_categories(regular_user: User):
    mock_todo_repo = AsyncMock()
    mock_category_repo = AsyncMock()
    mock_category_repo.get_by_ids.return_value = ["cat1", "cat2"]

    service = TodoService(repository=mock_todo_repo, category_repository=mock_category_repo)
    data = TodoCreate(title="Test", category_ids=[1, 2])

    await service.create_todo(owner_id=regular_user.id, data=data)

    mock_todo_repo.create.assert_called_once_with(
        owner_id=regular_user.id, data=data, categories=["cat1", "cat2"]
    )


async def test_create_todo_without_categories(regular_user):
    mock_todo_repo = AsyncMock()
    mock_category_repo = AsyncMock()
    mock_category_repo.get_by_ids.return_value = []

    service = TodoService(repository=mock_todo_repo, category_repository=mock_category_repo)
    data = TodoCreate(title="Test")

    await service.create_todo(owner_id=regular_user.id, data=data)

    mock_category_repo.get_by_ids.assert_called_once_with([])
    mock_todo_repo.create.assert_called_once_with(owner_id=1, data=data, categories=[])


# --- update_todo ---


async def test_update_todo_resolves_categories(regular_user: User):
    mock_todo_repo = AsyncMock()
    mock_category_repo = AsyncMock()
    mock_category_repo.get_by_ids.return_value = ["cat1", "cat2"]
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo.get_by_id.return_value = fake_todo
    mock_todo_repo.update.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=mock_category_repo)
    data = TodoUpdate(title="Test", category_ids=[1, 2])

    result = await service.update_todo(current_user=regular_user, entity_id=1, data=data)

    assert result == fake_todo
    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=True)
    mock_category_repo.get_by_ids.assert_called_once_with([1, 2])
    mock_todo_repo.update.assert_called_once_with(fake_todo, data, ["cat1", "cat2"])


async def test_update_todo_without_categories(regular_user: User):
    mock_todo_repo = AsyncMock()
    mock_category_repo = AsyncMock()
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo.get_by_id.return_value = fake_todo
    mock_todo_repo.update.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=mock_category_repo)
    data = TodoUpdate(title="Test")

    result = await service.update_todo(current_user=regular_user, entity_id=1, data=data)

    assert result == fake_todo
    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=False)
    mock_category_repo.get_by_ids.assert_not_called()
    mock_todo_repo.update.assert_called_once_with(fake_todo, data, None)


async def test_update_todo_of_another_user_raises_not_found_error(regular_user):
    mock_todo_repo = AsyncMock()
    mock_category_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = None

    service = TodoService(repository=mock_todo_repo, category_repository=mock_category_repo)

    with pytest.raises(NotFoundError):
        await service.update_todo(
            current_user=regular_user, entity_id=99, data=TodoUpdate(title="Hack")
        )

    mock_todo_repo.get_by_id.assert_called_once_with(
        owner_id=1, entity_id=99, with_categories=False
    )
    mock_category_repo.get_by_ids.assert_not_called()
    mock_todo_repo.update.assert_not_called()


async def test_update_todo_as_admin_on_another_users_todo(admin_user):
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = fake_todo
    mock_todo_repo.update.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())
    data = TodoUpdate(title="Edited")

    result = await service.update_todo(current_user=admin_user, entity_id=1, data=data)

    assert result == fake_todo
    mock_todo_repo.get_by_id.assert_called_once_with(
        owner_id=None, entity_id=1, with_categories=False
    )
    mock_todo_repo.update.assert_called_once_with(fake_todo, data, None)


# --- delete_todo ---


async def test_delete_todo_calls_repo_delete_when_found(regular_user: User):
    mock_todo_repo = AsyncMock()
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo.get_by_id.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    await service.delete_todo(current_user=regular_user, entity_id=1)

    mock_todo_repo.get_by_id.assert_called_once_with(owner_id=1, entity_id=1, with_categories=False)
    mock_todo_repo.delete.assert_called_once_with(fake_todo)


async def test_delete_todo_of_another_user_raises_not_found_error(regular_user):
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = None

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    with pytest.raises(NotFoundError):
        await service.delete_todo(current_user=regular_user, entity_id=99)

    mock_todo_repo.delete.assert_not_called()


async def test_delete_todo_as_admin_on_another_users_todo(admin_user):
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_by_id.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    await service.delete_todo(current_user=admin_user, entity_id=1)

    mock_todo_repo.get_by_id.assert_called_once_with(
        owner_id=None, entity_id=1, with_categories=False
    )
    mock_todo_repo.delete.assert_called_once_with(fake_todo)


# --- list_deleted_todos ---


@pytest.mark.parametrize(
    ("is_admin", "expected_owner_id"),
    [
        (False, 1),  # regular user → their own deleted todos, whatever owner_id they ask for
        (True, 5),  # admin → the requested owner_id is applied
    ],
)
async def test_list_deleted_todos_owner_filter_depends_on_role(is_admin, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    fake_todos = [Todo(id=1, owner_id=1, title="Test")]
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_all_deleted.return_value = fake_todos
    mock_todo_repo.count.return_value = 1

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    todos, total = await service.list_deleted_todos(
        current_user=user, skip=5, limit=10, owner_id=5, title="Te"
    )

    assert todos == fake_todos
    assert total == 1
    mock_todo_repo.get_all_deleted.assert_called_once_with(
        skip=5, limit=10, owner_id=expected_owner_id, title="Te"
    )
    mock_todo_repo.count.assert_called_once_with(
        owner_id=expected_owner_id, title="Te", is_deleted=True
    )


# --- restore_todo ---


@pytest.mark.parametrize(
    ("is_admin", "expected_owner_id"),
    [
        (False, 1),  # regular user → only their own deleted todo can be found
        (True, None),  # admin → anyone's deleted todo
    ],
)
async def test_restore_todo_restores_the_deleted_todo(is_admin, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    fake_todo = Todo(id=1, owner_id=1, title="Test")
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_deleted_by_id.return_value = fake_todo
    mock_todo_repo.restore.return_value = fake_todo

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    result = await service.restore_todo(current_user=user, entity_id=1)

    assert result == fake_todo
    mock_todo_repo.get_deleted_by_id.assert_called_once_with(
        owner_id=expected_owner_id, entity_id=1
    )
    mock_todo_repo.restore.assert_called_once_with(fake_todo)


async def test_restore_todo_not_found_raises_not_found_error(regular_user: User):
    """Missing, not deleted, or someone else's: the repository finds nothing either way."""
    mock_todo_repo = AsyncMock()
    mock_todo_repo.get_deleted_by_id.return_value = None

    service = TodoService(repository=mock_todo_repo, category_repository=AsyncMock())

    with pytest.raises(NotFoundError):
        await service.restore_todo(current_user=regular_user, entity_id=99)

    mock_todo_repo.restore.assert_not_called()
