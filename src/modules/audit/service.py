"""Service Layer (Logique métier pour les AuditLogs)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.core.config import settings
from src.core.exceptions import NotFoundError
from src.modules.audit.context import current_actor
from src.modules.audit.models import AuditAction, AuditLog, AuditResourceType
from src.modules.audit.repository import AuditLogRepository
from src.modules.users.models import User


class AuditLogService:
    def __init__(self, repository: AuditLogRepository) -> None:
        self.repository = repository

    async def list_audit_logs(
        self,
        current_user: User,
        skip: int = 0,
        limit: int = 25,
        resource_owner_id: int | None = None,
        action: AuditAction | None = None,
        resource_type: AuditResourceType | None = None,
        resource_id: int | None = None,
    ) -> tuple[Sequence[AuditLog], int]:
        """Fetch all audit logs with pagination."""
        resource_owner_id = current_user.id if not current_user.is_admin else resource_owner_id

        audit_logs = await self.repository.get_all(
            skip=skip,
            limit=limit,
            resource_owner_id=resource_owner_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        total = await self.repository.count(
            resource_owner_id=resource_owner_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        return (audit_logs, total)

    async def get_audit_log_or_404(self, current_user: User, entity_id: int) -> AuditLog:
        """Get an audit log by its ID or raise a 404 exception."""
        resource_owner_id = current_user.id if not current_user.is_admin else None
        audit_log = await self.repository.get_by_id(
            entity_id=entity_id, resource_owner_id=resource_owner_id
        )

        if not audit_log:
            raise NotFoundError(f"Audit log avec l'ID {entity_id} introuvable.")
        return audit_log

    async def record(
        self,
        *,
        resource_type: AuditResourceType,
        resource_id: int,
        resource_owner_id: int,
        action: AuditAction,
        changed_fields: list[str] | None = None,
    ) -> AuditLog:
        """Record a new audit log."""
        actor_id, auth_method, api_token_id = None, None, None
        if actor := current_actor.get():
            actor_id = actor.user_id
            auth_method = actor.auth_method
            api_token_id = actor.api_token_id

        return await self.repository.create(
            actor_id=actor_id,
            auth_method=auth_method,
            api_token_id=api_token_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_owner_id=resource_owner_id,
            action=action,
            changed_fields=changed_fields,
        )

    async def prune(self) -> dict[str, int]:
        """Prune old audit logs."""
        cutoff_date = datetime.now(UTC) - timedelta(days=settings.AUDIT_LOG_RETENTION_DAYS)
        logs_deleted = await self.repository.prune(before=cutoff_date)

        return {
            "audit_logs": logs_deleted,
        }
