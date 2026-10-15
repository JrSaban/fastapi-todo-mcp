"""Service Layer (Logique métier pour les ApprovalRequests)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import assert_never

from pydantic import JsonValue, TypeAdapter

from src.core.config import settings
from src.core.exceptions import ConflictError, NotFoundError
from src.modules.approvals.models import ApprovalAction, ApprovalRequest, ApprovalStatus
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.todos.repository import TodoRepository
from src.modules.users.models import User

_TODO_IDS = TypeAdapter(list[int])


class ApprovalRequestService:
    def __init__(
        self, repository: ApprovalRequestRepository, todo_repository: TodoRepository
    ) -> None:
        self.repository = repository
        self.todo_repository = todo_repository

    async def _decide(
        self, current_user: User, entity_id: int, status: ApprovalStatus
    ) -> ApprovalRequest:
        """Decide an approval request."""
        now = datetime.now(UTC)
        decided = await self.repository.decide(
            entity_id, owner_id=current_user.id, status=status, now=now
        )
        request = await self.repository.get_by_id(entity_id, owner_id=current_user.id)

        if request is None:
            raise NotFoundError(f"Demande avec l'ID {entity_id} introuvable.")

        if not decided:
            if request.status != ApprovalStatus.PENDING:
                raise ConflictError("Demande déjà traitée.")
            raise ConflictError("Demande expirée.")

        return request

    async def _execute_request(self, request: ApprovalRequest) -> dict[str, JsonValue]:
        """Execute an approval request."""
        match request.action:
            case ApprovalAction.DELETE_COMPLETED_TODOS:
                todo_ids = _TODO_IDS.validate_python(request.payload["todo_ids"])
                count = await self.todo_repository.delete_completed(
                    todo_ids=todo_ids, owner_id=request.owner_id
                )
                return {"deleted_count": count}
            case _:
                assert_never(request.action)

    async def list_approval_requests(
        self,
        current_user: User,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        status: ApprovalStatus | None = None,
    ) -> tuple[Sequence[ApprovalRequest], int]:
        """Fetch all approval requests with pagination."""
        owner_id = current_user.id if not current_user.is_admin else owner_id

        requests = await self.repository.get_all(
            skip=skip,
            limit=limit,
            owner_id=owner_id,
            status=status,
        )
        total = await self.repository.count(owner_id=owner_id, status=status)

        return (requests, total)

    async def get_approval_request_or_404(
        self, current_user: User, entity_id: int
    ) -> ApprovalRequest:
        """Get an approval request by its ID or raise a 404 exception."""
        owner_id = current_user.id if not current_user.is_admin else None
        request = await self.repository.get_by_id(entity_id=entity_id, owner_id=owner_id)

        if not request:
            raise NotFoundError(f"Demande avec l'ID {entity_id} introuvable.")
        return request

    async def request_delete_completed_todos(self, current_user: User) -> ApprovalRequest:
        """Request deletion of completed todos."""
        completed_todos = await self.todo_repository.get_completed_ids(owner_id=current_user.id)
        if not completed_todos:
            raise ConflictError("Aucune tâche terminée trouvée.")

        request = await self.repository.create(
            owner_id=current_user.id,
            action=ApprovalAction.DELETE_COMPLETED_TODOS,
            payload={"todo_ids": list(completed_todos)},
            expires_at=datetime.now(UTC) + timedelta(days=settings.APPROVAL_REQUEST_EXPIRE_DAYS),
        )
        return request

    async def approve(self, current_user: User, entity_id: int) -> ApprovalRequest:
        """Approve an approval request."""
        request = await self._decide(current_user, entity_id, ApprovalStatus.APPROVED)

        result = await self._execute_request(request)
        await self.repository.set_result(entity=request, result=result)

        return request

    async def reject(self, current_user: User, entity_id: int) -> ApprovalRequest:
        """Reject an approval request."""
        return await self._decide(current_user, entity_id, ApprovalStatus.REJECTED)

    async def prune_expired_requests(self, before: datetime) -> int:
        """Prune expired approval requests."""
        return await self.repository.pruned_expired(before)