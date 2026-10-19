"""
Jobs du module API token, exécutés par le worker Taskiq.

Chaque job ouvre sa propre session et commit lui-même : le worker n'a ni FastAPI ni DbSessionDep.
"""

from src.core.broker import broker
from src.core.database import async_session_factory
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.api_tokens.service import ApiTokenService
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.service import AuditLogService
from src.modules.users.repository import UserRepository


@broker.task(task_name="api_tokens:prune_expired", schedule=[{"cron": "0 5 * * 0"}])
async def prune_expired_api_tokens() -> dict[str, int]:
    """Prune expired API tokens."""
    async with async_session_factory() as session:
        api_token_service = ApiTokenService(
            repository=ApiTokenRepository(session),
            user_repository=UserRepository(session),
            audit_service=AuditLogService(repository=AuditLogRepository(session)),
        )

        result = await api_token_service.prune_expired()
        await session.commit()
    return result
