"""Tests d'intégration des demandes d'approbation : suppression en masse des todos terminés."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.security import generate_api_token, hash_token
from src.modules.api_tokens.models import ApiTokenUsage
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.approvals.models import ApprovalAction, ApprovalRequest, ApprovalStatus
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.approvals.tasks import prune_expired_requests
from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate
from src.modules.users.models import User

DELETE_COMPLETED_PATH = "/api/v1/todos/delete-completed"
REQUESTS_PATH = "/api/v1/approval-requests"


async def _issue_token(session: AsyncSession, owner_id: int) -> str:
    """Store an API token and return its plain value.

    Committed, like everything a token request must see: such a request makes the middlewares
    open their own session, and closing it rolls back the single shared test connection.
    """
    plain = generate_api_token()
    await ApiTokenRepository(session).create(
        owner_id=owner_id,
        name="Agent",
        usage=ApiTokenUsage.API,
        token_hash=hash_token(plain),
        expires_at=None,
    )
    await session.commit()
    return plain


def _agent_headers(plain: str, key: str = "bulk-1") -> dict[str, str]:
    """A token request on an idempotent route must carry a key."""
    return {"Authorization": f"Bearer {plain}", "Idempotency-Key": key}


async def _todo(
    session: AsyncSession, owner_id: int, title: str, *, completed: bool = True
) -> Todo:
    todo = await TodoRepository(session).create(owner_id, TodoCreate(title=title), [])
    todo.is_completed = completed
    await session.flush()
    return todo


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


async def _is_deleted(session: AsyncSession, todo_id: int) -> bool:
    result = await session.execute(select(Todo.deleted_at).where(Todo.id == todo_id))
    return result.scalar_one() is not None


def _as_utc(value: str) -> datetime:
    """SQLite gives timestamps back without a timezone; they are UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- POST /todos/delete-completed, login session: executed directly ---


async def test_delete_completed_with_a_session_deletes_directly(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    """Only the user's completed, live todos go; nothing waits for an approval."""
    done_1 = await _todo(db_session, current_user.id, "Fait 1")
    done_2 = await _todo(db_session, current_user.id, "Fait 2")
    still_open = await _todo(db_session, current_user.id, "À faire", completed=False)
    someone_elses = await _todo(db_session, other_user.id, "Fait par un autre")

    response = await authenticated_client.post(DELETE_COMPLETED_PATH)

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 2}
    assert await _is_deleted(db_session, done_1.id)
    assert await _is_deleted(db_session, done_2.id)
    assert not await _is_deleted(db_session, still_open.id)
    assert not await _is_deleted(db_session, someone_elses.id)
    assert (await authenticated_client.get(REQUESTS_PATH)).json()["total"] == 0


async def test_delete_completed_with_a_session_and_nothing_completed_returns_zero(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Executing nothing is a valid result for a human: no error, unlike the agent's request."""
    await _todo(db_session, current_user.id, "À faire", completed=False)

    response = await authenticated_client.post(DELETE_COMPLETED_PATH)

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 0}


async def test_deleted_completed_todos_can_be_restored(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A bulk deletion is a soft delete like any other: each todo can be brought back."""
    todo = await _todo(db_session, current_user.id, "Fait")
    await authenticated_client.post(DELETE_COMPLETED_PATH)

    response = await authenticated_client.post(f"/api/v1/todos/{todo.id}/restore")

    assert response.status_code == 200


# --- POST /todos/delete-completed, API token: an approval request ---


async def test_delete_completed_with_an_api_token_creates_a_pending_request(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """202: nothing is deleted, the request freezes the IDs the human will approve."""
    done_1 = await _todo(db_session, current_user.id, "Fait 1")
    done_2 = await _todo(db_session, current_user.id, "Fait 2")
    await _todo(db_session, current_user.id, "À faire", completed=False)
    plain = await _issue_token(db_session, current_user.id)
    before = datetime.now(UTC)

    response = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert response.status_code == 202
    data = response.json()
    assert data["owner_id"] == current_user.id
    assert data["action"] == "delete_completed_todos"
    assert data["status"] == "pending"
    assert sorted(data["payload"]["todo_ids"]) == sorted([done_1.id, done_2.id])
    assert data["result"] is None
    assert data["decided_at"] is None
    expected = before + timedelta(days=settings.APPROVAL_REQUEST_EXPIRE_DAYS)
    margin = timedelta(seconds=5)
    assert expected - margin <= _as_utc(data["expires_at"]) <= expected + margin

    todos = await client.get("/api/v1/todos", headers=_agent_headers(plain))
    assert todos.json()["total"] == 3


async def test_delete_completed_with_an_api_token_and_nothing_completed_returns_409(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert response.status_code == 409


async def test_delete_completed_with_an_api_token_requires_an_idempotency_key(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The route is idempotent: an agent retrying blindly must not create a second request."""
    await _todo(db_session, current_user.id, "Fait")
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        DELETE_COMPLETED_PATH, headers={"Authorization": f"Bearer {plain}"}
    )

    assert response.status_code == 400


async def test_delete_completed_retry_with_the_same_key_replays_the_request(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    await _todo(db_session, current_user.id, "Fait")
    plain = await _issue_token(db_session, current_user.id)

    first = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))
    second = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert second.status_code == 202
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]


async def test_agent_requests_and_human_approves(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The whole flow: the agent's token asks, the human's session approves."""
    done = await _todo(db_session, current_user.id, "Fait")
    plain = await _issue_token(db_session, current_user.id)

    requested = await authenticated_client.post(
        DELETE_COMPLETED_PATH, headers=_agent_headers(plain)
    )
    assert requested.status_code == 202
    assert not await _is_deleted(db_session, done.id)

    approved = await authenticated_client.post(f"{REQUESTS_PATH}/{requested.json()['id']}/approve")

    assert approved.status_code == 200
    assert approved.json()["result"] == {"deleted_count": 1}
    assert await _is_deleted(db_session, done.id)


# --- approve / reject ---


async def test_approve_deletes_the_frozen_todos_only(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """A todo completed after the request isn't in the approved list, so it stays."""
    done_1 = await _todo(db_session, current_user.id, "Fait 1")
    done_2 = await _todo(db_session, current_user.id, "Fait 2")
    request = await _pending_request(db_session, current_user.id, [done_1.id, done_2.id])
    completed_later = await _todo(db_session, current_user.id, "Fait après la demande")

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approved"
    assert data["decided_at"] is not None
    assert data["result"] == {"deleted_count": 2}
    assert await _is_deleted(db_session, done_1.id)
    assert await _is_deleted(db_session, done_2.id)
    assert not await _is_deleted(db_session, completed_later.id)


async def test_approve_skips_todos_reopened_or_deleted_since_the_request(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The approved list is re-checked: deleted_count is what was really deleted."""
    still_done = await _todo(db_session, current_user.id, "Toujours fait")
    reopened = await _todo(db_session, current_user.id, "Rouvert")
    already_deleted = await _todo(db_session, current_user.id, "Déjà supprimé")
    request = await _pending_request(
        db_session, current_user.id, [still_done.id, reopened.id, already_deleted.id]
    )
    reopened.is_completed = False
    await TodoRepository(db_session).delete(already_deleted)

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert response.json()["result"] == {"deleted_count": 1}
    assert not await _is_deleted(db_session, reopened.id)


async def test_approve_twice_returns_409(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The second approval matches no pending row: the action never runs twice."""
    done = await _todo(db_session, current_user.id, "Fait")
    request = await _pending_request(db_session, current_user.id, [done.id])

    first = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")
    second = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert first.status_code == 200
    assert second.status_code == 409


async def test_reject_deletes_nothing_and_closes_the_request(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    done = await _todo(db_session, current_user.id, "Fait")
    request = await _pending_request(db_session, current_user.id, [done.id])

    rejected = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/reject")
    approved_after = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decided_at"] is not None
    assert rejected.json()["result"] is None
    assert approved_after.status_code == 409
    assert not await _is_deleted(db_session, done.id)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_deciding_an_expired_request_returns_409(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    decision: str,
) -> None:
    done = await _todo(db_session, current_user.id, "Fait")
    request = await _pending_request(
        db_session, current_user.id, [done.id], expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/{decision}")

    assert response.status_code == 409
    assert "expirée" in response.json()["detail"]
    assert not await _is_deleted(db_session, done.id)


@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_deciding_someone_elses_request_returns_404(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    other_user: User,
    decision: str,
) -> None:
    done = await _todo(db_session, other_user.id, "Fait par un autre")
    request = await _pending_request(db_session, other_user.id, [done.id])

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{request.id}/{decision}")

    assert response.status_code == 404
    assert not await _is_deleted(db_session, done.id)


async def test_an_admin_cannot_approve_someone_elses_request(
    authenticated_admin: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    """Only the owner decides: an admin can read the request, not approve it."""
    done = await _todo(db_session, other_user.id, "Fait par un autre")
    request = await _pending_request(db_session, other_user.id, [done.id])

    response = await authenticated_admin.post(f"{REQUESTS_PATH}/{request.id}/approve")

    assert response.status_code == 404
    assert not await _is_deleted(db_session, done.id)


async def test_an_api_token_cannot_approve(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The point of the whole mechanism: the agent can't approve its own request."""
    done = await _todo(db_session, current_user.id, "Fait")
    request = await _pending_request(db_session, current_user.id, [done.id])
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        f"{REQUESTS_PATH}/{request.id}/approve",
        headers={"Authorization": f"Bearer {plain}"},
    )

    assert response.status_code == 403
    assert not await _is_deleted(db_session, done.id)
    result = await db_session.execute(
        select(ApprovalRequest.status).where(ApprovalRequest.id == request.id)
    )
    assert result.scalar_one() == ApprovalStatus.PENDING


async def test_an_api_token_can_reject(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Rejecting executes nothing, so the agent may cancel a request when the user asks."""
    done = await _todo(db_session, current_user.id, "Fait")
    request = await _pending_request(db_session, current_user.id, [done.id])
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        f"{REQUESTS_PATH}/{request.id}/reject",
        headers={"Authorization": f"Bearer {plain}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert not await _is_deleted(db_session, done.id)


async def test_an_api_token_cannot_reject_someone_elses_request(
    client: AsyncClient, db_session: AsyncSession, current_user: User, other_user: User
) -> None:
    request = await _pending_request(db_session, other_user.id, [])
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(
        f"{REQUESTS_PATH}/{request.id}/reject",
        headers={"Authorization": f"Bearer {plain}"},
    )

    assert response.status_code == 404


# --- one pending request per action ---


async def test_a_second_agent_request_while_one_is_pending_returns_409_with_its_id(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """The agent is pointed to the request already waiting, instead of piling up new ones."""
    done = await _todo(db_session, current_user.id, "Fait")
    pending = await _pending_request(db_session, current_user.id, [done.id])
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert response.status_code == 409
    assert f"ID: {pending.id}" in response.json()["detail"]


@pytest.mark.parametrize("closed", ["expired", "rejected", "approved"])
async def test_a_closed_request_doesnt_block_a_new_one(
    client: AsyncClient, db_session: AsyncSession, current_user: User, closed: str
) -> None:
    """Only a request that can still be approved counts: an expired one is still `pending` in
    the database, yet it must not block the user forever."""
    done = await _todo(db_session, current_user.id, "Fait")
    if closed == "expired":
        await _pending_request(
            db_session,
            current_user.id,
            [done.id],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    else:
        request = await _pending_request(db_session, current_user.id, [done.id])
        request.status = ApprovalStatus(closed)
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert response.status_code == 202


async def test_another_users_pending_request_doesnt_block(
    client: AsyncClient, db_session: AsyncSession, current_user: User, other_user: User
) -> None:
    await _pending_request(db_session, other_user.id, [])
    await _todo(db_session, current_user.id, "Fait")
    plain = await _issue_token(db_session, current_user.id)

    response = await client.post(DELETE_COMPLETED_PATH, headers=_agent_headers(plain))

    assert response.status_code == 202


# --- the computed "expired" status ---


async def _three_requests(session: AsyncSession, owner_id: int) -> dict[str, ApprovalRequest]:
    """A request waiting, one pending in the database but past its expiry, one rejected."""
    waiting = await _pending_request(session, owner_id, [])
    expired = await _pending_request(
        session, owner_id, [], expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    rejected = await _pending_request(session, owner_id, [])
    rejected.status = ApprovalStatus.REJECTED
    await session.flush()
    return {"pending": waiting, "expired": expired, "rejected": rejected}


async def test_responses_show_an_expired_request_as_expired(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    requests = await _three_requests(db_session, current_user.id)

    listed = await authenticated_client.get(REQUESTS_PATH)
    one = await authenticated_client.get(f"{REQUESTS_PATH}/{requests['expired'].id}")

    statuses = {item["id"]: item["status"] for item in listed.json()["items"]}
    assert statuses == {request.id: state for state, request in requests.items()}
    assert one.json()["status"] == "expired"


@pytest.mark.parametrize("state", ["pending", "expired", "rejected"])
async def test_the_status_filter_tells_pending_from_expired(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    state: str,
) -> None:
    """`pending` means "can still be approved": an expired request only matches `expired`.
    The page and its total agree."""
    requests = await _three_requests(db_session, current_user.id)

    response = await authenticated_client.get(f"{REQUESTS_PATH}?status={state}")

    data = response.json()
    assert [item["id"] for item in data["items"]] == [requests[state].id]
    assert data["items"][0]["status"] == state
    assert data["total"] == 1


async def test_an_unknown_status_filter_returns_422(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.get(f"{REQUESTS_PATH}?status=cancelled")

    assert response.status_code == 422


async def test_rejecting_an_expired_request_returns_409(
    authenticated_client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    requests = await _three_requests(db_session, current_user.id)

    response = await authenticated_client.post(f"{REQUESTS_PATH}/{requests['expired'].id}/reject")

    assert response.status_code == 409


# --- approvals:prune_expired ---


async def test_prune_deletes_requests_expired_beyond_retention_whatever_their_status(
    db_session: AsyncSession, current_user: User
) -> None:
    """The retention runs from expires_at, not from the decision: a request decided long ago
    stays as long as an undecided one, so the user keeps the same history of both."""
    now = datetime.now(UTC)
    beyond = now - timedelta(days=settings.APPROVAL_REQUEST_RETENTION_DAYS + 1)
    within = now - timedelta(days=settings.APPROVAL_REQUEST_RETENTION_DAYS - 1)
    old_ids = []
    for status in ApprovalStatus:
        old = await _pending_request(db_session, current_user.id, [], expires_at=beyond)
        old.status = status
        old_ids.append(old.id)
    recent = await _pending_request(db_session, current_user.id, [], expires_at=within)
    recent.status = ApprovalStatus.APPROVED
    waiting = await _pending_request(db_session, current_user.id, [])
    await db_session.commit()

    result = await (await prune_expired_requests.kiq()).wait_result()

    assert result.return_value == {"approval_requests": len(old_ids)}
    remaining = await db_session.execute(select(ApprovalRequest.id))
    assert set(remaining.scalars()) == {recent.id, waiting.id}


def test_prune_is_scheduled_monthly() -> None:
    """The 1st of each month, 03:00 UTC."""
    assert prune_expired_requests.labels["schedule"] == [{"cron": "0 3 1 * *"}]


async def test_deciding_an_unknown_request_returns_404(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.post(f"{REQUESTS_PATH}/9999/approve")

    assert response.status_code == 404


# --- reads ---


async def test_list_returns_only_own_requests_and_filters_by_status(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    current_user: User,
    other_user: User,
) -> None:
    done = await _todo(db_session, current_user.id, "Fait")
    pending = await _pending_request(db_session, current_user.id, [done.id])
    rejected = await _pending_request(db_session, current_user.id, [done.id])
    rejected.status = ApprovalStatus.REJECTED
    await _pending_request(db_session, other_user.id, [])
    await db_session.flush()

    everything = await authenticated_client.get(REQUESTS_PATH)
    only_pending = await authenticated_client.get(f"{REQUESTS_PATH}?status=pending")

    assert everything.json()["total"] == 2
    assert {item["id"] for item in everything.json()["items"]} == {pending.id, rejected.id}
    assert [item["id"] for item in only_pending.json()["items"]] == [pending.id]
    assert only_pending.json()["total"] == 1


async def test_list_as_admin_returns_everyone_and_filters_by_owner(
    authenticated_admin: AsyncClient,
    db_session: AsyncSession,
    current_admin_user: User,
    other_user: User,
) -> None:
    await _pending_request(db_session, current_admin_user.id, [])
    theirs = await _pending_request(db_session, other_user.id, [])

    everyone = await authenticated_admin.get(REQUESTS_PATH)
    filtered = await authenticated_admin.get(f"{REQUESTS_PATH}?owner_id={other_user.id}")

    assert everyone.json()["total"] == 2
    assert [item["id"] for item in filtered.json()["items"]] == [theirs.id]


async def test_get_someone_elses_request_returns_404(
    authenticated_client: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    request = await _pending_request(db_session, other_user.id, [])

    response = await authenticated_client.get(f"{REQUESTS_PATH}/{request.id}")

    assert response.status_code == 404


async def test_get_someone_elses_request_as_admin_success(
    authenticated_admin: AsyncClient, db_session: AsyncSession, other_user: User
) -> None:
    request = await _pending_request(db_session, other_user.id, [])

    response = await authenticated_admin.get(f"{REQUESTS_PATH}/{request.id}")

    assert response.status_code == 200
    assert response.json()["owner_id"] == other_user.id


async def test_an_api_token_can_follow_its_requests(
    client: AsyncClient, db_session: AsyncSession, current_user: User
) -> None:
    """Reading is open to a token: the agent checks whether its request was decided."""
    request = await _pending_request(db_session, current_user.id, [])
    plain = await _issue_token(db_session, current_user.id)
    headers = {"Authorization": f"Bearer {plain}"}

    one = await client.get(f"{REQUESTS_PATH}/{request.id}", headers=headers)
    listed = await client.get(REQUESTS_PATH, headers=headers)

    assert one.status_code == 200
    assert one.json()["status"] == "pending"
    assert listed.json()["total"] == 1
