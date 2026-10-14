from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.core.config import settings
from src.core.exceptions import ConflictError, NotFoundError
from src.modules.approvals.models import ApprovalAction, ApprovalRequest, ApprovalStatus
from src.modules.approvals.service import ApprovalRequestService
from src.modules.users.models import User


@pytest.fixture
def regular_user() -> User:
    return User(id=1, email="user@example.com", hashed_password="test", is_admin=False)


@pytest.fixture
def admin_user() -> User:
    return User(id=2, email="admin@example.com", hashed_password="test", is_admin=True)


def _request(status: ApprovalStatus = ApprovalStatus.PENDING, payload=None) -> ApprovalRequest:
    return ApprovalRequest(
        id=1,
        owner_id=1,
        action=ApprovalAction.DELETE_COMPLETED_TODOS,
        status=status,
        payload={"todo_ids": [3, 4]} if payload is None else payload,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


def _service(
    repository: AsyncMock, todo_repository: AsyncMock | None = None
) -> ApprovalRequestService:
    return ApprovalRequestService(repository, todo_repository or AsyncMock())


# --- request_delete_completed_todos ---


async def test_request_freezes_the_completed_todo_ids(regular_user: User):
    repository = AsyncMock()
    todo_repository = AsyncMock()
    todo_repository.get_completed_ids.return_value = [4, 3]

    before = datetime.now(UTC)
    await _service(repository, todo_repository).request_delete_completed_todos(regular_user)

    todo_repository.get_completed_ids.assert_called_once_with(owner_id=1)
    kwargs = repository.create.call_args.kwargs
    assert kwargs["owner_id"] == 1
    assert kwargs["action"] == ApprovalAction.DELETE_COMPLETED_TODOS
    assert kwargs["payload"] == {"todo_ids": [4, 3]}
    expected = before + timedelta(days=settings.APPROVAL_REQUEST_EXPIRE_DAYS)
    assert expected <= kwargs["expires_at"] <= expected + timedelta(seconds=5)


async def test_request_with_no_completed_todo_raises_conflict(regular_user: User):
    """An empty request would leave the human nothing to approve."""
    repository = AsyncMock()
    todo_repository = AsyncMock()
    todo_repository.get_completed_ids.return_value = []

    with pytest.raises(ConflictError):
        await _service(repository, todo_repository).request_delete_completed_todos(regular_user)

    repository.create.assert_not_called()


# --- approve ---


@pytest.mark.parametrize("user_fixture", ["regular_user", "admin_user"])
async def test_approve_decides_executes_and_stores_the_result(
    user_fixture: str, request: pytest.FixtureRequest
):
    """The decision always filters on the caller's own ID, admin included: only the owner
    approves their requests. The deletion targets the request's owner."""
    user: User = request.getfixturevalue(user_fixture)
    approval_request = _request()
    repository = AsyncMock()
    repository.decide.return_value = True
    repository.get_by_id.return_value = approval_request
    todo_repository = AsyncMock()
    todo_repository.delete_completed.return_value = 2

    result = await _service(repository, todo_repository).approve(user, 1)

    assert result is approval_request
    assert repository.decide.call_args.kwargs["owner_id"] == user.id
    assert repository.decide.call_args.kwargs["status"] == ApprovalStatus.APPROVED
    repository.get_by_id.assert_called_once_with(1, owner_id=user.id)
    todo_repository.delete_completed.assert_called_once_with(todo_ids=[3, 4], owner_id=1)
    repository.set_result.assert_called_once_with(
        entity=approval_request, result={"deleted_count": 2}
    )


async def test_approve_reads_the_request_after_deciding(regular_user: User):
    """The row is loaded after the conditional UPDATE, so it carries the new status."""
    calls: list[str] = []
    repository = AsyncMock()
    repository.decide.side_effect = lambda *a, **k: calls.append("decide") or True
    repository.get_by_id.side_effect = lambda *a, **k: calls.append("get_by_id") or _request()

    await _service(repository).approve(regular_user, 1)

    assert calls == ["decide", "get_by_id"]


async def test_approve_missing_request_raises_not_found(regular_user: User):
    """Missing or someone else's: the owner-filtered read finds nothing either way."""
    repository = AsyncMock()
    repository.decide.return_value = False
    repository.get_by_id.return_value = None
    todo_repository = AsyncMock()

    with pytest.raises(NotFoundError):
        await _service(repository, todo_repository).approve(regular_user, 99)

    todo_repository.delete_completed.assert_not_called()


@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
async def test_approve_already_decided_request_raises_conflict(
    regular_user: User, status: ApprovalStatus
):
    repository = AsyncMock()
    repository.decide.return_value = False
    repository.get_by_id.return_value = _request(status=status)
    todo_repository = AsyncMock()

    with pytest.raises(ConflictError, match="déjà"):
        await _service(repository, todo_repository).approve(regular_user, 1)

    todo_repository.delete_completed.assert_not_called()
    repository.set_result.assert_not_called()


async def test_approve_pending_request_not_decided_means_expired(regular_user: User):
    """The row exists, is the caller's and is still pending, yet the UPDATE matched nothing:
    the only condition left is the expiry. No date is compared in Python."""
    repository = AsyncMock()
    repository.decide.return_value = False
    repository.get_by_id.return_value = _request()
    todo_repository = AsyncMock()

    with pytest.raises(ConflictError, match="expirée"):
        await _service(repository, todo_repository).approve(regular_user, 1)

    todo_repository.delete_completed.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},  # no todo_ids at all
        {"todo_ids": ["not-an-id"]},
        {"todo_ids": None},
    ],
)
async def test_approve_with_a_corrupted_payload_fails_without_deleting(
    regular_user: User, payload: dict
):
    """A payload read back from the database is validated: a corrupted one makes the approval
    fail (and the transaction roll back) instead of silently deleting nothing."""
    repository = AsyncMock()
    repository.decide.return_value = True
    repository.get_by_id.return_value = _request(payload=payload)
    todo_repository = AsyncMock()

    with pytest.raises((KeyError, ValidationError)):
        await _service(repository, todo_repository).approve(regular_user, 1)

    todo_repository.delete_completed.assert_not_called()
    repository.set_result.assert_not_called()


# --- reject ---


async def test_reject_decides_without_executing(regular_user: User):
    approval_request = _request(status=ApprovalStatus.REJECTED)
    repository = AsyncMock()
    repository.decide.return_value = True
    repository.get_by_id.return_value = approval_request
    todo_repository = AsyncMock()

    result = await _service(repository, todo_repository).reject(regular_user, 1)

    assert result is approval_request
    assert repository.decide.call_args.kwargs["status"] == ApprovalStatus.REJECTED
    todo_repository.delete_completed.assert_not_called()
    repository.set_result.assert_not_called()


# --- reads ---


@pytest.mark.parametrize(
    ("is_admin", "expected_owner_id"),
    [
        (False, 1),  # regular user → their own requests, whatever owner_id they ask for
        (True, 5),  # admin → the requested owner_id is applied
    ],
)
async def test_list_owner_filter_depends_on_role(is_admin: bool, expected_owner_id: int):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    repository = AsyncMock()
    repository.get_all.return_value = []
    repository.count.return_value = 0

    await _service(repository).list_approval_requests(
        current_user=user, owner_id=5, status=ApprovalStatus.PENDING
    )

    repository.get_all.assert_called_once_with(
        skip=0, limit=25, owner_id=expected_owner_id, status=ApprovalStatus.PENDING
    )
    repository.count.assert_called_once_with(
        owner_id=expected_owner_id, status=ApprovalStatus.PENDING
    )


@pytest.mark.parametrize(("is_admin", "expected_owner_id"), [(False, 1), (True, None)])
async def test_get_or_404_owner_filter_depends_on_role(is_admin: bool, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    repository = AsyncMock()
    repository.get_by_id.return_value = None

    with pytest.raises(NotFoundError):
        await _service(repository).get_approval_request_or_404(current_user=user, entity_id=1)

    repository.get_by_id.assert_called_once_with(entity_id=1, owner_id=expected_owner_id)
