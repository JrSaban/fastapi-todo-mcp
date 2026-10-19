from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.modules.users.models import User
from src.modules.users.schemas import (
    UserCreate,
    UserEmailUpdate,
    UserPasswordUpdate,
    UserSelfEmailUpdate,
    UserUpdate,
)
from src.modules.users.service import UserService

# --- list_users ---


async def test_list_users_by_admin_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    users = [admin_user]
    total = 1
    mock_user_repo.get_all.return_value = users
    mock_user_repo.count.return_value = total

    result = await service.list_users(admin_user)

    assert result == (users, total)
    mock_user_repo.get_all.assert_called_once_with(
        skip=0,
        limit=25,
        email=None,
        name=None,
        pseudo=None,
        is_active=None,
        is_admin=None,
    )
    mock_user_repo.count.assert_called_once()


async def test_list_users_by_no_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )

    with pytest.raises(ForbiddenError):
        await service.list_users(current_user)

    mock_user_repo.get_all.assert_not_called()
    mock_user_repo.count.assert_not_called()


# --- get_user_or_404 ---


async def test_get_me_by_user_returns_current_user():
    mock_user_repo = AsyncMock()
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="password",
    )
    mock_user_repo.get_by_id.return_value = current_user

    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())

    result = await service.get_user_or_404(current_user, current_user.id)

    assert result == current_user
    mock_user_repo.get_by_id.assert_called_once_with(current_user.id)


async def test_get_user_or_404_raises_when_not_found():
    mock_user_repo = AsyncMock()
    mock_user_repo.get_by_id.return_value = None
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )

    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())

    with pytest.raises(NotFoundError):
        await service.get_user_or_404(current_user, 2)

    mock_user_repo.get_by_id.assert_called_once_with(2)


async def test_get_user_by_admin_user_returns_user():
    mock_user_repo = AsyncMock()
    admin_user = User(
        id=1,
        email="admin@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
        hashed_password="password",
    )
    fake_user = User(
        id=2,
        email="jane@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="password",
    )
    mock_user_repo.get_by_id.return_value = fake_user

    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())

    result = await service.get_user_or_404(admin_user, fake_user.id)

    assert admin_user.is_admin
    assert admin_user.id != fake_user.id
    assert result == fake_user
    mock_user_repo.get_by_id.assert_called_once_with(fake_user.id)


async def test_get_user_by_no_admin_user_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="password",
    )
    fake_user_id = 2

    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())

    with pytest.raises(ForbiddenError):
        await service.get_user_or_404(current_user, fake_user_id)

    assert not current_user.is_admin
    assert current_user.id != fake_user_id
    mock_user_repo.get_by_id.assert_not_called()


# --- create_user ---


@patch("src.modules.users.service.hash_password", return_value="hashedpassword")
async def test_create_user_success(mock_hash_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    user_data = UserCreate(
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        password="password",
        confirm_password="password",
    )
    fake_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashedpassword",
    )
    mock_user_repo.create.return_value = fake_user

    result = await service.create_user(user_data)

    assert result == fake_user
    mock_user_repo.create.assert_called_once_with(user_data, "hashedpassword")


@patch("src.modules.users.service.hash_password", return_value="hashedpassword")
async def test_create_user_with_existing_email_raises_conflict_error(mock_hash_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    user_data = UserCreate(
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        password="password",
        confirm_password="password",
    )
    mock_user_repo.create.side_effect = IntegrityError(
        "stmt", {}, Exception("uq_users_email_lower")
    )

    with pytest.raises(ConflictError):
        await service.create_user(user_data)

    mock_user_repo.create.assert_called_once_with(user_data, "hashedpassword")


@patch("src.modules.users.service.hash_password", return_value="hashedpassword")
async def test_create_user_with_existing_pseudo_raises_conflict_error(mock_hash_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    user_data = UserCreate(
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        pseudo="test",
        password="password",
        confirm_password="password",
    )
    mock_user_repo.create.side_effect = IntegrityError(
        "stmt", {}, Exception("uq_users_pseudo_lower")
    )

    with pytest.raises(ConflictError):
        await service.create_user(user_data)

    mock_user_repo.create.assert_called_once_with(user_data, "hashedpassword")


async def test_create_user_with_unrelated_integrity_error_reraises():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    user_data = UserCreate(
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        password="password",
        confirm_password="password",
    )
    mock_user_repo.create.side_effect = IntegrityError(
        "stmt", {}, Exception("some other constraint")
    )
    with pytest.raises(IntegrityError):
        await service.create_user(user_data)


# --- update_user ---


async def test_update_user_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    user_data = UserUpdate(
        first_name="Jane",
        last_name="Doe",
    )
    updated_user = User(
        id=1,
        email="test@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    mock_user_repo.get_by_id.return_value = current_user
    mock_user_repo.update.return_value = updated_user

    result = await service.update_user(current_user, current_user.id, user_data)

    assert not current_user.is_admin
    assert current_user.id == updated_user.id
    assert current_user.first_name != updated_user.first_name
    assert current_user.last_name == updated_user.last_name
    assert result == updated_user
    mock_user_repo.get_by_id.assert_called_once_with(current_user.id)
    mock_user_repo.update.assert_called_once_with(current_user, user_data)


async def test_update_user_by_admin_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    user_data = UserUpdate(
        first_name="Jane",
        last_name="Doe",
    )
    target_user = User(
        id=2,
        email="test@gmail.com",
        first_name="Paul",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    updated_user = User(
        id=2,
        email="test@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    mock_user_repo.get_by_id.return_value = target_user
    mock_user_repo.update.return_value = updated_user

    result = await service.update_user(admin_user, target_user.id, user_data)

    assert admin_user.is_admin
    assert target_user.id == updated_user.id
    assert target_user.first_name != updated_user.first_name
    assert target_user.last_name == updated_user.last_name
    assert result == updated_user
    mock_user_repo.get_by_id.assert_called_once_with(target_user.id)
    mock_user_repo.update.assert_called_once_with(target_user, user_data)


async def test_update_user_by_no_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    user_data = UserUpdate(first_name="Jane", pseudo="new_pseudo")
    fake_user_id = 2

    with pytest.raises(ForbiddenError):
        await service.update_user(current_user, fake_user_id, user_data)

    assert not current_user.is_admin
    assert current_user.id != fake_user_id
    mock_user_repo.get_by_id.assert_not_called()
    mock_user_repo.update.assert_not_called()


async def test_update_user_with_existing_pseudo_raises_conflict_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    user_data = UserUpdate(pseudo="new_pseudo")
    mock_user_repo.get_by_id.return_value = current_user
    mock_user_repo.update.side_effect = IntegrityError(
        "stmt", {}, Exception("uq_users_pseudo_lower")
    )

    with pytest.raises(ConflictError):
        await service.update_user(current_user, current_user.id, user_data)

    mock_user_repo.get_by_id.assert_called_once_with(current_user.id)
    mock_user_repo.update.assert_called_once_with(current_user, user_data)


# --- update_email ---


@patch("src.modules.users.service.verify_password", return_value=True)
async def test_update_email_success(mock_verify_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="old@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_password",
    )
    user_data = UserSelfEmailUpdate(new_email="new@gmail.com", current_password="password")
    updated_user = User(
        id=1,
        email="new@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_password",
    )
    mock_user_repo.update_email.return_value = updated_user

    result = await service.update_email(current_user, user_data)

    assert result == updated_user
    mock_verify_password.assert_called_once_with(
        user_data.current_password, current_user.hashed_password
    )
    mock_user_repo.update_email.assert_called_once_with(current_user, user_data.new_email)


@patch("src.modules.users.service.verify_password", return_value=False)
async def test_update_email_with_incorrect_password_raises_forbidden_error(mock_verify_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="old@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_password",
    )
    user_data = UserSelfEmailUpdate(new_email="new@gmail.com", current_password="wrong_password")

    with pytest.raises(ForbiddenError):
        await service.update_email(current_user, user_data)

    mock_verify_password.assert_called_once_with(
        user_data.current_password, current_user.hashed_password
    )
    mock_user_repo.update_email.assert_not_called()


@patch("src.modules.users.service.verify_password", return_value=True)
async def test_update_email_with_existing_email_raises_conflict_error(mock_verify_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="old@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_password",
    )
    user_data = UserSelfEmailUpdate(new_email="taken@gmail.com", current_password="password")
    mock_user_repo.update_email.side_effect = IntegrityError(
        "stmt", {}, Exception("uq_users_email_lower")
    )

    with pytest.raises(ConflictError):
        await service.update_email(current_user, user_data)


# --- update_password ---


@patch("src.modules.users.service.hash_password", return_value="hashed_new_password")
@patch("src.modules.users.service.verify_password", return_value=True)
async def test_update_password_success(mock_verify_password, mock_hash_password):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_old_password",
    )
    user_data = UserPasswordUpdate(
        old_password="old_password",
        new_password="new_password",
        confirm_new_password="new_password",
    )
    updated_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_new_password",
    )
    mock_user_repo.update_password.return_value = updated_user

    result = await service.update_password(current_user, user_data)

    assert result == updated_user
    assert current_user.hashed_password != updated_user.hashed_password
    mock_verify_password.assert_called_once_with(
        user_data.old_password, current_user.hashed_password
    )
    mock_hash_password.assert_called_once_with(user_data.new_password)
    mock_user_repo.update_password.assert_called_once_with(
        current_user, updated_user.hashed_password
    )


@patch("src.modules.users.service.hash_password")
@patch("src.modules.users.service.verify_password", return_value=False)
async def test_update_password_with_incorrect_old_password_raises_forbidden_error(
    mock_verify_password,
    mock_hash_password,
):
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
        hashed_password="hashed_old_password",
    )
    user_data = UserPasswordUpdate(
        old_password="wrong_password",
        new_password="new_password",
        confirm_new_password="new_password",
    )

    with pytest.raises(ForbiddenError):
        await service.update_password(current_user, user_data)

    mock_verify_password.assert_called_once_with(
        user_data.old_password, current_user.hashed_password
    )
    mock_hash_password.assert_not_called()
    assert mock_verify_password.return_value is False
    mock_user_repo.update_password.assert_not_called()


# --- set_user_email ---


async def test_set_user_email_by_admin_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="admin@gmail.com",
        first_name="Admin",
        last_name="User",
        is_active=True,
        is_admin=True,
    )
    target_user = User(
        id=2,
        email="old@gmail.com",
        first_name="Target",
        last_name="User",
        is_active=True,
        is_admin=False,
    )
    updated_user = User(
        id=2,
        email="new@gmail.com",
        first_name="Target",
        last_name="User",
        is_active=True,
        is_admin=False,
    )
    user_data = UserEmailUpdate(new_email="new@gmail.com")
    mock_user_repo.get_by_id.return_value = target_user
    mock_user_repo.update_email.return_value = updated_user

    result = await service.set_user_email(admin_user, target_user.id, user_data)

    assert result == updated_user
    mock_user_repo.get_by_id.assert_called_once_with(target_user.id)
    mock_user_repo.update_email.assert_called_once_with(target_user, user_data.new_email)


async def test_set_user_email_by_non_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    regular_user = User(
        id=1,
        email="user@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    user_data = UserEmailUpdate(new_email="new@gmail.com")

    with pytest.raises(ForbiddenError):
        await service.set_user_email(regular_user, 2, user_data)

    mock_user_repo.get_by_id.assert_not_called()
    mock_user_repo.update_email.assert_not_called()


async def test_set_user_email_on_own_id_raises_forbidden_error():
    """An admin can't use this admin-only route on their own id — they must go
    through /me/email with their password instead."""
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="admin@gmail.com",
        first_name="Admin",
        last_name="User",
        is_active=True,
        is_admin=True,
    )
    user_data = UserEmailUpdate(new_email="new@gmail.com")

    with pytest.raises(ForbiddenError):
        await service.set_user_email(admin_user, admin_user.id, user_data)

    mock_user_repo.get_by_id.assert_not_called()
    mock_user_repo.update_email.assert_not_called()


async def test_set_user_email_with_existing_email_raises_conflict_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="admin@gmail.com",
        first_name="Admin",
        last_name="User",
        is_active=True,
        is_admin=True,
    )
    target_user = User(
        id=2,
        email="old@gmail.com",
        first_name="Target",
        last_name="User",
        is_active=True,
        is_admin=False,
    )
    user_data = UserEmailUpdate(new_email="taken@gmail.com")
    mock_user_repo.get_by_id.return_value = target_user
    mock_user_repo.update_email.side_effect = IntegrityError(
        "stmt", {}, Exception("uq_users_email_lower")
    )

    with pytest.raises(ConflictError):
        await service.set_user_email(admin_user, target_user.id, user_data)


# --- set_user_active ---


async def test_set_user_active_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    target_user = User(
        id=2,
        email="jane@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    updated_user = User(
        id=2,
        email="jane@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=False,
        is_admin=False,
    )
    mock_user_repo.get_by_id.return_value = target_user
    mock_user_repo.set_active.return_value = updated_user

    result = await service.set_user_active(admin_user, target_user.id, False)

    assert admin_user.is_admin
    assert target_user.is_active != updated_user.is_active
    assert result == updated_user
    mock_user_repo.get_by_id.assert_called_once_with(target_user.id)
    mock_user_repo.set_active.assert_called_once_with(target_user, False)


async def test_set_user_active_by_no_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    target_user_id = 2

    with pytest.raises(ForbiddenError):
        await service.set_user_active(current_user, target_user_id, False)

    assert not current_user.is_admin
    mock_user_repo.get_by_id.assert_not_called()
    mock_user_repo.set_active.assert_not_called()


async def test_set_user_inactive_last_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )

    mock_user_repo.get_by_id.return_value = admin_user
    mock_user_repo.count.return_value = 1

    with pytest.raises(ForbiddenError):
        await service.set_user_active(admin_user, admin_user.id, False)

    assert admin_user.is_admin
    mock_user_repo.get_by_id.assert_called_once_with(admin_user.id)
    mock_user_repo.count.assert_called_once_with(is_admin=True, is_active=True)
    mock_user_repo.set_active.assert_not_called()


# --- set_user_admin ---


async def test_set_user_admin_success():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    target_user = User(
        id=2,
        email="jane@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    updated_user = User(
        id=2,
        email="jane@gmail.com",
        first_name="Jane",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )
    mock_user_repo.get_by_id.return_value = target_user
    mock_user_repo.set_admin.return_value = updated_user

    result = await service.set_user_admin(admin_user, target_user.id, True)

    assert admin_user.is_admin
    assert target_user.is_admin != updated_user.is_admin
    assert result == updated_user
    mock_user_repo.get_by_id.assert_called_once_with(target_user.id)
    mock_user_repo.set_admin.assert_called_once_with(target_user, True)


async def test_set_user_admin_by_no_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    current_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=False,
    )
    target_user_id = 2

    with pytest.raises(ForbiddenError):
        await service.set_user_admin(current_user, target_user_id, True)

    assert not current_user.is_admin
    mock_user_repo.get_by_id.assert_not_called()
    mock_user_repo.set_admin.assert_not_called()


async def test_set_user_no_admin_last_admin_raises_forbidden_error():
    mock_user_repo = AsyncMock()
    service = UserService(repository=mock_user_repo, audit_service=AsyncMock())
    admin_user = User(
        id=1,
        email="test@gmail.com",
        first_name="John",
        last_name="Doe",
        is_active=True,
        is_admin=True,
    )

    mock_user_repo.get_by_id.return_value = admin_user
    mock_user_repo.count.return_value = 1

    with pytest.raises(ForbiddenError):
        await service.set_user_admin(admin_user, admin_user.id, False)

    assert admin_user.is_admin
    mock_user_repo.get_by_id.assert_called_once_with(admin_user.id)
    mock_user_repo.count.assert_called_once_with(is_admin=True, is_active=True)
    mock_user_repo.set_admin.assert_not_called()
