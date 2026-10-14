"""Repository Pattern pour l'accès aux données de base."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, NamedTuple, cast

from pydantic import BaseModel
from sqlalchemy import CursorResult, Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from src.core.database import Base


class FilterParams(NamedTuple):
    column: InstrumentedAttribute
    value: object
    op: Literal["eq", "ilike"]


class BaseRepository[ModelT: Base]:
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    async def get_by_id(self, entity_id: int) -> ModelT | None:
        """Get model's entity by its ID"""
        query = select(self.model).where(self.model.id == entity_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update(self, entity: ModelT, data: BaseModel) -> ModelT:
        """Update an entity"""
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(entity, field, value)

        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)

        return entity

    async def delete(self, entity: ModelT) -> None:
        """Delete an entity"""
        await self.session.delete(entity)
        await self.session.flush()

    async def paginate(self, query: Select, skip: int, limit: int) -> Sequence[ModelT]:
        """Paginate a query"""
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def count_query(self, query: Select) -> int:
        """Count a query"""
        result = await self.session.execute(query)
        return result.scalar_one()

    def _apply_filter_params(self, query: Select, filters: list[FilterParams]) -> Select:
        """Apply filters to a query"""
        for filter_param in filters:
            if filter_param.value is None:
                continue

            match filter_param.op:
                case "eq":
                    query = query.where(filter_param.column == filter_param.value)
                case "ilike":
                    escaped_value = self.escape_ilike_value(str(filter_param.value))
                    query = query.where(
                        filter_param.column.ilike(f"%{escaped_value}%", escape="\\")
                    )
                case _:
                    raise ValueError(f"Unsupported operator: {filter_param.op}")
        return query

    @staticmethod
    def escape_ilike_value(value: str) -> str:
        """Escape \\, % and _ so an ilike search matches them literally, not as wildcards."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SoftDeleteRepository[ModelT: Base](BaseRepository[ModelT]):
    """Repository of a soft-deletable model: adds hard deletion
    of rows soft-deleted long enough ago."""

    soft_delete_column: str = "deleted_at"

    async def prune_soft_deleted(self, before: datetime) -> int:
        """Hard-delete the rows soft-deleted before the given date; return how many were deleted."""
        column = getattr(self.model, self.soft_delete_column)
        query = delete(self.model).where(column < before)
        result = await self.session.execute(query)
        return cast(CursorResult, result).rowcount

    async def restore(self, entity: ModelT) -> ModelT:
        """Restore a soft-deleted entity"""
        setattr(entity, self.soft_delete_column, None)
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)

        return entity
