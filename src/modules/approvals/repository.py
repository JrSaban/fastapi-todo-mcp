"""Repository Pattern pour l'accès aux données de Approval requests."""

from sqlalchemy import delete
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from pydantic import JsonValue
from sqlalchemy import CursorResult, Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import BaseRepository, FilterParams
from src.modules.approvals.models import ApprovalAction, ApprovalRequest, ApprovalStatus


class ApprovalRequestRepository(BaseRepository[ApprovalRequest]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ApprovalRequest)

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        status: ApprovalStatus | None = None,
    ) -> Sequence[ApprovalRequest]:
        """Get paginated approval requests."""
        query = select(ApprovalRequest).order_by(
            ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()
        )
        query = self._apply_filters(
            query,
            owner_id=owner_id,
            status=status,
        )

        return await self.paginate(query, skip, limit)

    async def get_by_id(  # pyrefly: ignore[bad-override]
        self,
        entity_id: int,
        *,
        owner_id: int | None,
    ) -> ApprovalRequest | None:
        """Get approval request by its ID."""
        query = select(ApprovalRequest).where(ApprovalRequest.id == entity_id)

        if owner_id is not None:
            query = query.where(ApprovalRequest.owner_id == owner_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        owner_id: int,
        action: ApprovalAction,
        payload: dict[str, JsonValue],
        expires_at: datetime,
    ) -> ApprovalRequest:
        """Create and persist a new approval request."""
        approval_request = ApprovalRequest(
            owner_id=owner_id,
            action=action,
            payload=payload,
            expires_at=expires_at,
        )
        self.session.add(approval_request)
        # Génère l'ID via PostgreSQL sans commiter la transaction globale
        await self.session.flush()
        await self.session.refresh(approval_request)
        return approval_request

    async def decide(
        self,
        entity_id: int,
        *,
        owner_id: int,
        status: ApprovalStatus,
        now: datetime,
    ) -> bool:
        """Decide an approval request."""
        query = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == entity_id,
                ApprovalRequest.owner_id == owner_id,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at > now,
            )
            .values(
                status=status,
                decided_at=now,
            )
            .execution_options(synchronize_session="fetch")
        )
        result = await self.session.execute(query)
        return cast(CursorResult[ApprovalRequest], result).rowcount == 1

    async def set_result(self, entity: ApprovalRequest, result: dict[str, JsonValue]) -> None:
        """Set the result of an approval request."""
        entity.result = result
        await self.session.flush()

    async def pruned_expired(self, before: datetime) -> int:
        """Prune expired approval requests."""
        query = delete(ApprovalRequest).where(ApprovalRequest.expires_at < before)
        result = await self.session.execute(query)
        return cast(CursorResult[ApprovalRequest], result).rowcount

    async def count(
        self,
        *,
        owner_id: int | None = None,
        action: ApprovalAction | None = None,
        status: ApprovalStatus | None = None,
    ) -> int:
        """Count approvals requests with filters."""
        query = select(func.count()).select_from(ApprovalRequest)
        query = self._apply_filters(
            query,
            owner_id=owner_id,
            action=action,
            status=status,
        )

        return await self.count_query(query)

    def _apply_filters(
        self,
        query: Select,
        *,
        owner_id: int | None = None,
        action: ApprovalAction | None = None,
        status: ApprovalStatus | None = None,
    ) -> Select:
        """Helper which applies filters on a query."""
        return self._apply_filter_params(
            query,
            [
                FilterParams(column=ApprovalRequest.owner_id, value=owner_id, op="eq"),
                FilterParams(column=ApprovalRequest.status, value=status, op="eq"),
                FilterParams(column=ApprovalRequest.action, value=action, op="eq"),
            ],
        )
