from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from src.modules.audit.context import Actor, current_actor
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditResourceType
from src.modules.audit.service import AuditLogService


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
