"""
Jobs du module audit, exécutés par le worker Taskiq.

Chaque job ouvre sa propre session et commit lui-même : le worker n'a ni FastAPI ni DbSessionDep.
"""

from src.core.broker import broker
from src.core.database import async_session_factory
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.service import AuditLogService


@broker.task(task_name="audit:prune_old", schedule=[{"cron": "0 1 1 * *"}])
async def prune_old_audit_logs() -> dict[str, int]:
    """Prune old audit logs."""
    async with async_session_factory() as session:
        audit_service = AuditLogService(
            repository=AuditLogRepository(session),
        )

        result = await audit_service.prune()
        await session.commit()
    return result
