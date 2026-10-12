"""
Repository Pattern pour l'accès aux données de API Tokens.
Encapsule les requêtes SQL (SQLAlchemy 2.0 select, add, delete).
"""

from sqlalchemy import CursorResult
from typing import cast
from sqlalchemy import delete
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import BaseRepository
from src.modules.api_tokens.models import ApiToken
from src.modules.users.models import User


class ApiTokenRepository(BaseRepository[ApiToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ApiToken)

    async def list_for_owner(
        self,
        owner_id: int,
    ) -> Sequence[ApiToken]:
        """Get all API tokens for an owner."""
        query = select(ApiToken).where(ApiToken.owner_id == owner_id).order_by(ApiToken.id.desc())

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_owned(self, *, token_id: int, owner_id: int) -> ApiToken | None:
        """Get token by its ID if owned by the user."""
        query = select(ApiToken).where(ApiToken.id == token_id, ApiToken.owner_id == owner_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_active_by_hash(self, token_hash: str, now: datetime) -> ApiToken | None:
        """Get active token by its hash."""
        query = select(ApiToken).where(
            ApiToken.token_hash == token_hash,
            or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_usable_owner_id(self, token_hash: str, now: datetime) -> int | None:
        """Get owner ID for a usable token hash."""
        query = (
            select(ApiToken.owner_id)
            .join(User, User.id == ApiToken.owner_id)
            .where(
                ApiToken.token_hash == token_hash,
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
                User.is_active.is_(True),
                User.is_admin.is_(False),
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def touch_last_used(self, token_id: int, now: datetime, older_than: datetime) -> None:
        """Update last_used_at for a token if older than older_than."""
        query = (
            update(ApiToken)
            .where(
                ApiToken.id == token_id,
                or_(ApiToken.last_used_at.is_(None), ApiToken.last_used_at < older_than),
            )
            .values(last_used_at=now)
        )
        await self.session.execute(query)

    async def create(
        self, *, owner_id: int, name: str, token_hash: str, expires_at: datetime | None
    ) -> ApiToken:
        """Create and persist a new API token."""
        token = ApiToken(
            owner_id=owner_id,
            name=name,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(token)
        # Génère l'ID via PostgreSQL sans commiter la transaction globale
        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def count_active(self, owner_id: int, now: datetime) -> int:
        """Count active tokens for an owner."""
        query = (
            select(func.count())
            .select_from(ApiToken)
            .where(
                ApiToken.owner_id == owner_id,
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
            )
        )

        return await self.count_query(query)

    async def delete_all_for_owner(self, owner_id: int) -> int:
        """Delete all API tokens for an owner."""
        query = delete(ApiToken).where(ApiToken.owner_id == owner_id)
        result = await self.session.execute(query)
        return cast(CursorResult, result).rowcount
        