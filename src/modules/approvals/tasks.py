"""
Jobs du module approvals, exécutés par le worker Taskiq.

Chaque job ouvre sa propre session et commit lui-même : le worker n'a ni FastAPI ni DbSessionDep.
"""

from src.core.broker import broker
from src.core.database import async_session_factory
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.approvals.service import ApprovalRequestService
from src.modules.todos.repository import TodoRepository
from src.modules.users.repository import UserRepository


@broker.task(task_name="approvals:prune_expired", schedule=[{"cron": "0 3 1 * *"}])
async def prune_expired_requests() -> dict[str, int]:
    """Prune expired approval requests."""
    async with async_session_factory() as session:
        approval_service = ApprovalRequestService(
            repository=ApprovalRequestRepository(session),
            todo_repository=TodoRepository(session),
            user_repository=UserRepository(session),
        )

        result = await approval_service.prune_expired()
        await session.commit()
    return result
