from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from src.modules.api_tokens.models import ApiToken
from src.modules.approvals.models import ApprovalAction, ApprovalRequest
from src.modules.audit.context import Actor, current_actor
from src.modules.audit.contracts import Auditable
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditResourceType
from src.modules.audit.service import AuditLogService
from src.modules.categories.models import Category
from src.modules.todos.models import Todo
from src.modules.users.models import User


@pytest.fixture
def actor() -> Iterator[None]:
    """Reset the request's actor after the test, whatever the test set it to."""
    token = current_actor.set(None)
    yield
    current_actor.reset(token)


async def _record(repository: AsyncMock) -> None:
    await AuditLogService(repository).record(
        resource_type=AuditResourceType.TODO,
        resource_id=7,
        resource_owner_id=3,
        action=AuditAction.UPDATED,
        changed_fields=["title"],
    )


@pytest.mark.usefixtures("actor")
async def test_record_without_an_actor_is_a_system_write():
    """A scheduled job has no request, hence no actor: the entry is attributed to the system."""
    repository = AsyncMock()

    await _record(repository)

    kwargs = repository.create.call_args.kwargs
    assert kwargs["actor_id"] is None
    assert kwargs["auth_method"] is None
    assert kwargs["api_token_id"] is None
    assert kwargs["resource_type"] == AuditResourceType.TODO
    assert kwargs["resource_id"] == 7
    assert kwargs["resource_owner_id"] == 3
    assert kwargs["action"] == AuditAction.UPDATED
    assert kwargs["changed_fields"] == ["title"]


@pytest.mark.usefixtures("actor")
async def test_record_takes_the_login_session_actor():
    current_actor.set(Actor(user_id=5, auth_method=AuditAuthMethod.SESSION, api_token_id=None))
    repository = AsyncMock()

    await _record(repository)

    kwargs = repository.create.call_args.kwargs
    assert kwargs["actor_id"] == 5
    assert kwargs["auth_method"] == AuditAuthMethod.SESSION
    assert kwargs["api_token_id"] is None


@pytest.mark.parametrize(
    ("entity", "resource_type", "owner_id"),
    [
        (Todo(id=7, owner_id=3, title="T"), AuditResourceType.TODO, 3),
        (Category(id=7, name="C", created_by_id=3), AuditResourceType.CATEGORY, 3),
        (User(id=7, email="u@test.com", hashed_password="h"), AuditResourceType.USER, 7),
        (ApiToken(id=7, owner_id=3, name="A", token_hash="h"), AuditResourceType.API_TOKEN, 3),
        (
            ApprovalRequest(id=7, owner_id=3, action=ApprovalAction.DELETE_COMPLETED_TODOS),
            AuditResourceType.APPROVAL_REQUEST,
            3,
        ),
    ],
)
@pytest.mark.usefixtures("actor")
async def test_log_methods_read_the_resource_from_the_entity(
    entity: Auditable, resource_type: AuditResourceType, owner_id: int
):
    """Each audited model names its resource type and its owner: a category belongs to its
    creator, an account to itself."""
    repository = AsyncMock()

    await AuditLogService(repository).log_deleted(entity)

    kwargs = repository.create.call_args.kwargs
    assert kwargs["resource_type"] == resource_type
    assert kwargs["resource_id"] == 7
    assert kwargs["resource_owner_id"] == owner_id
    assert kwargs["action"] == AuditAction.DELETED


@pytest.mark.parametrize(
    ("method", "action"),
    [
        ("log_created", AuditAction.CREATED),
        ("log_deleted", AuditAction.DELETED),
        ("log_restored", AuditAction.RESTORED),
        ("log_approved", AuditAction.APPROVED),
        ("log_rejected", AuditAction.REJECTED),
    ],
)
@pytest.mark.usefixtures("actor")
async def test_each_log_method_records_its_action(method: str, action: AuditAction):
    repository = AsyncMock()

    await getattr(AuditLogService(repository), method)(Todo(id=7, owner_id=3, title="T"))

    assert repository.create.call_args.kwargs["action"] == action
    assert repository.create.call_args.kwargs["changed_fields"] is None


@pytest.mark.usefixtures("actor")
async def test_log_updated_records_the_changed_fields():
    repository = AsyncMock()

    await AuditLogService(repository).log_updated(
        Todo(id=7, owner_id=3, title="T"), ["title", "is_completed"]
    )

    assert repository.create.call_args.kwargs["action"] == AuditAction.UPDATED
    assert repository.create.call_args.kwargs["changed_fields"] == ["title", "is_completed"]


@pytest.mark.parametrize("changes", [[], None])
@pytest.mark.usefixtures("actor")
async def test_log_updated_without_changes_records_nothing(changes: list[str] | None):
    """An update that changed nothing isn't a modification: no entry."""
    repository = AsyncMock()

    result = await AuditLogService(repository).log_updated(
        Todo(id=7, owner_id=3, title="T"), changes
    )

    assert result is None
    repository.create.assert_not_called()


@pytest.mark.usefixtures("actor")
async def test_record_takes_the_api_token_actor_and_its_token():
    """Which token acted is what lets the user revoke the right one."""
    current_actor.set(Actor(user_id=5, auth_method=AuditAuthMethod.API_TOKEN, api_token_id=42))
    repository = AsyncMock()

    await _record(repository)

    kwargs = repository.create.call_args.kwargs
    assert kwargs["actor_id"] == 5
    assert kwargs["auth_method"] == AuditAuthMethod.API_TOKEN
    assert kwargs["api_token_id"] == 42
