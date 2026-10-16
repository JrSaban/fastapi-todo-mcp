from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import ForbiddenError, InvalidPasswordError
from src.modules.api_tokens.schemas import ApiTokenCreate
from src.modules.api_tokens.service import ApiTokenService
from src.modules.users.models import User


def _user(*, is_admin: bool = False) -> User:
    return User(id=1, email="user@test.com", hashed_password="hashed", is_admin=is_admin)


def _service() -> tuple[ApiTokenService, AsyncMock, AsyncMock]:
    token_repo, user_repo = AsyncMock(), AsyncMock()
    token_repo.count_active.return_value = 0
    service = ApiTokenService(
        repository=token_repo, user_repository=user_repo, audit_service=AsyncMock()
    )
    return service, token_repo, user_repo


DATA = ApiTokenCreate(name="Agent", current_password="secret123")


# --- create_token ---


@patch("src.modules.api_tokens.service.verify_password", return_value=True)
async def test_create_token_locks_the_user_before_counting(mock_verify) -> None:
    """The row lock must come before the count: that is what makes a concurrent creation
    wait and then see this one. SQLite ignores FOR UPDATE, so only the order can be
    checked here."""
    service, token_repo, user_repo = _service()
    calls = MagicMock()
    calls.attach_mock(user_repo.lock, "lock")
    calls.attach_mock(token_repo.count_active, "count_active")

    await service.create_token(_user(), DATA)

    assert [name for name, *_ in calls.mock_calls] == ["lock", "count_active"]


@patch("src.modules.api_tokens.service.verify_password", return_value=False)
async def test_create_token_wrong_password_locks_nothing(mock_verify) -> None:
    """A wrong password is rejected before the lock, so it never makes anyone wait."""
    service, token_repo, user_repo = _service()

    with pytest.raises(InvalidPasswordError):
        await service.create_token(_user(), DATA)

    user_repo.lock.assert_not_called()
    token_repo.create.assert_not_called()


@patch("src.modules.api_tokens.service.verify_password")
async def test_create_token_by_admin_is_refused_before_the_password_check(mock_verify) -> None:
    """An admin gets a plain ForbiddenError, never InvalidPasswordError: the router must not
    count it as a wrong password."""
    service, token_repo, user_repo = _service()

    with pytest.raises(ForbiddenError) as exc_info:
        await service.create_token(_user(is_admin=True), DATA)

    assert not isinstance(exc_info.value, InvalidPasswordError)
    mock_verify.assert_not_called()
    user_repo.lock.assert_not_called()
