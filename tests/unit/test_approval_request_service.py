from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.core.config import settings
from src.core.exceptions import ConflictError, NotFoundError
from src.modules.approvals.models import ApprovalAction, ApprovalRequest, ApprovalStatus
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.approvals.schemas import ApprovalRequestResponse, ApprovalRequestState
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
    repository: AsyncMock,
    todo_repository: AsyncMock | None = None,
    user_repository: AsyncMock | None = None,
) -> ApprovalRequestService:
    return ApprovalRequestService(
        repository, todo_repository or AsyncMock(), user_repository or AsyncMock()
    )


def _repository_without_pending_request() -> AsyncMock:
    """An AsyncMock's methods return a (truthy) mock by default: say explicitly that no request
    is pending, or every creation would hit the one-pending-request conflict."""
    repository = AsyncMock()
    repository.get_pending.return_value = None
    return repository


# --- request_delete_completed_todos ---


async def test_request_freezes_the_completed_todo_ids(regular_user: User):
    repository = _repository_without_pending_request()
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
    repository = _repository_without_pending_request()
    todo_repository = AsyncMock()
    todo_repository.get_completed_ids.return_value = []

    with pytest.raises(ConflictError):
        await _service(repository, todo_repository).request_delete_completed_todos(regular_user)

    repository.create.assert_not_called()


async def test_request_while_one_is_pending_raises_conflict_with_its_id(regular_user: User):
    """One pending request per action: the agent is pointed to the existing one, and nothing
    else is read or written."""
    repository = AsyncMock()
    repository.get_pending.return_value = _request()
    todo_repository = AsyncMock()

    with pytest.raises(ConflictError, match="ID: 1"):
        await _service(repository, todo_repository).request_delete_completed_todos(regular_user)

    kwargs = repository.get_pending.call_args.kwargs
    assert kwargs["owner_id"] == 1
    assert kwargs["action"] == ApprovalAction.DELETE_COMPLETED_TODOS
    todo_repository.get_completed_ids.assert_not_called()
    repository.create.assert_not_called()


async def test_request_locks_the_user_before_looking_for_a_pending_request(regular_user: User):
    """The lock serializes concurrent creations, so the second one sees the first one's request.
    SQLite ignores FOR UPDATE: only the order can be checked here."""
    calls: list[str] = []
    repository = AsyncMock()
    repository.get_pending.side_effect = lambda **k: calls.append("get_pending")
    user_repository = AsyncMock()
    user_repository.lock.side_effect = lambda *a: calls.append("lock")
    todo_repository = AsyncMock()
    todo_repository.get_completed_ids.return_value = [3]

    await _service(repository, todo_repository, user_repository).request_delete_completed_todos(
        regular_user
    )

    user_repository.lock.assert_called_once_with(1)
    assert calls == ["lock", "get_pending"]


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
    todo_repository.delete_completed.return_value = [3, 4]

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
        current_user=user, owner_id=5, status=ApprovalRequestState.PENDING
    )

    page_kwargs = repository.get_all.call_args.kwargs
    count_kwargs = repository.count.call_args.kwargs
    assert page_kwargs["owner_id"] == count_kwargs["owner_id"] == expected_owner_id
    assert page_kwargs["status"] == count_kwargs["status"] == ApprovalRequestState.PENDING


async def test_list_passes_the_same_now_to_the_page_and_its_total(regular_user: User):
    """A request expiring between the two queries would otherwise be counted in one and not
    the other."""
    repository = AsyncMock()
    repository.get_all.return_value = []
    repository.count.return_value = 0

    await _service(repository).list_approval_requests(
        current_user=regular_user, status=ApprovalRequestState.EXPIRED
    )

    now = repository.get_all.call_args.kwargs["now"]
    assert isinstance(now, datetime)
    assert repository.count.call_args.kwargs["now"] is now


@pytest.mark.parametrize(("is_admin", "expected_owner_id"), [(False, 1), (True, None)])
async def test_get_or_404_owner_filter_depends_on_role(is_admin: bool, expected_owner_id):
    user = User(id=1, email="test@example.com", hashed_password="test", is_admin=is_admin)
    repository = AsyncMock()
    repository.get_by_id.return_value = None

    with pytest.raises(NotFoundError):
        await _service(repository).get_approval_request_or_404(current_user=user, entity_id=1)

    repository.get_by_id.assert_called_once_with(entity_id=1, owner_id=expected_owner_id)


# --- prune_expired ---


async def test_prune_expired_keeps_the_retention_after_expires_at():
    """The cut-off is applied to expires_at, so a request stays APPROVAL_REQUEST_RETENTION_DAYS
    after its expiry, whatever its status."""
    repository = AsyncMock()
    repository.prune_expired.return_value = 3

    before = datetime.now(UTC)
    result = await _service(repository).prune_expired()

    assert result == {"approval_requests": 3}
    cutoff = repository.prune_expired.call_args.kwargs["before"]
    expected = before - timedelta(days=settings.APPROVAL_REQUEST_RETENTION_DAYS)
    assert expected <= cutoff <= expected + timedelta(seconds=5)


# --- the status filter (repository) ---


@pytest.mark.parametrize("state", [ApprovalRequestState.PENDING, ApprovalRequestState.EXPIRED])
def test_filtering_on_a_date_dependent_status_without_now_fails(state: ApprovalRequestState):
    """Without `now`, these two filters can't be applied: failing beats silently dropping the
    filter and returning every request."""
    repository = ApprovalRequestRepository(AsyncMock())

    with pytest.raises(ValueError):
        repository._apply_filters(select(ApprovalRequest), status=state)


@pytest.mark.parametrize("state", [ApprovalRequestState.APPROVED, ApprovalRequestState.REJECTED])
def test_filtering_on_a_decided_status_needs_no_now(state: ApprovalRequestState):
    repository = ApprovalRequestRepository(AsyncMock())

    query = repository._apply_filters(select(ApprovalRequest), status=state)

    assert "status" in str(query.whereclause)


# --- the status shown in responses ---


def _response(status: ApprovalStatus, expires_at: datetime) -> ApprovalRequestResponse:
    request = _request(status=status)
    request.expires_at = expires_at
    request.created_at = datetime.now(UTC)
    return ApprovalRequestResponse.model_validate(request)


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime.now(UTC) - timedelta(minutes=1),  # aware, as Postgres returns it
        datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),  # naive, as SQLite does
    ],
)
def test_a_pending_request_past_its_expiry_is_shown_expired(expires_at: datetime):
    assert _response(ApprovalStatus.PENDING, expires_at).status == ApprovalRequestState.EXPIRED


def test_a_pending_request_before_its_expiry_stays_pending():
    response = _response(ApprovalStatus.PENDING, datetime.now(UTC) + timedelta(days=1))

    assert response.status == ApprovalRequestState.PENDING


@pytest.mark.parametrize("status", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_a_decided_request_keeps_its_status_after_its_expiry(status: ApprovalStatus):
    """Expiry only concerns a request nobody decided: an approval stays an approval."""
    response = _response(status, datetime.now(UTC) - timedelta(days=1))

    assert response.status == ApprovalRequestState(status.value)
