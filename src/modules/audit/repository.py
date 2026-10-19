"""Repository Pattern pour l'accès aux données de l'audit log."""

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import BaseRepository, FilterParams
from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditLog, AuditResourceType


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLog)

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 25,
        resource_owner_id: int | None = None,
        action: AuditAction | None = None,
        resource_type: AuditResourceType | None = None,
        resource_id: int | None = None,
    ) -> Sequence[AuditLog]:
        """Get paginated audit logs."""
        query = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        query = self._apply_filters(
            query,
            resource_owner_id=resource_owner_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        return await self.paginate(query, skip, limit)

    async def get_by_id(  # pyrefly: ignore[bad-override]
        self,
        entity_id: int,
        *,
        resource_owner_id: int | None,
    ) -> AuditLog | None:
        """Get audit log by its ID."""
        query = select(AuditLog).where(AuditLog.id == entity_id)

        if resource_owner_id is not None:
            query = query.where(AuditLog.resource_owner_id == resource_owner_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        actor_id: int | None,
        auth_method: AuditAuthMethod | None,
        api_token_id: int | None,
        resource_type: AuditResourceType,
        resource_id: int,
        resource_owner_id: int,
        action: AuditAction,
        changed_fields: list[str] | None,
    ) -> AuditLog:
        """Create and persist a new audit log."""
        audit_log = AuditLog(
            actor_id=actor_id,
            auth_method=auth_method,
            api_token_id=api_token_id,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_owner_id=resource_owner_id,
            action=action,
            changed_fields=changed_fields,
        )

        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def prune(self, before: datetime) -> int:
        """Prune old audit logs."""
        query = delete(AuditLog).where(AuditLog.created_at < before)
        result = await self.session.execute(query)
        return cast(CursorResult[AuditLog], result).rowcount

    async def count(
        self,
        *,
        resource_owner_id: int | None = None,
        action: AuditAction | None = None,
        resource_type: AuditResourceType | None = None,
        resource_id: int | None = None,
    ) -> int:
        """Count audit logs with filters."""
        query = select(func.count()).select_from(AuditLog)
        query = self._apply_filters(
            query,
            resource_owner_id=resource_owner_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        return await self.count_query(query)

    def _apply_filters(
        self,
        query: Select,
        *,
        resource_owner_id: int | None = None,
        action: AuditAction | None = None,
        resource_type: AuditResourceType | None = None,
        resource_id: int | None = None,
    ) -> Select:
        """Helper which applies filters on a query."""
        query = self._apply_filter_params(
            query,
            [
                FilterParams(column=AuditLog.resource_owner_id, value=resource_owner_id, op="eq"),
                FilterParams(column=AuditLog.action, value=action, op="eq"),
                FilterParams(column=AuditLog.resource_type, value=resource_type, op="eq"),
                FilterParams(column=AuditLog.resource_id, value=resource_id, op="eq"),
            ],
        )

        return query
