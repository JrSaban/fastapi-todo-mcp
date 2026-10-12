"""Repository Pattern pour l'accès aux données de User."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repository import BaseRepository, FilterParams
from src.modules.users.models import User
from src.modules.users.schemas import UserCreate


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 25,
        email: str | None = None,
        name: str | None = None,
        pseudo: str | None = None,
        is_active: bool | None = None,
        is_admin: bool | None = None,
    ) -> Sequence[User]:
        """Get all users"""
        query = select(User).order_by(User.id.desc())
        query = self._apply_filters(
            query,
            email=email,
            name=name,
            pseudo=pseudo,
            is_active=is_active,
            is_admin=is_admin,
        )

        return await self.paginate(query, skip, limit)

    async def get_by_email(self, email: str) -> User | None:
        """Get an user by its email"""
        query = select(User).where(func.lower(User.email) == email.strip().lower())

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, data: UserCreate, hashed_password: str) -> User:
        """Create a new user."""
        user = User(
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
            pseudo=data.pseudo,
            hashed_password=hashed_password,
        )

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def update_password(self, user: User, hashed_password: str) -> User:
        """Update an existing user's password."""
        user.hashed_password = hashed_password

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def update_email(self, user: User, email: str) -> User:
        """Update an existing user's email."""
        user.email = email

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def set_active(self, user: User, is_active: bool) -> User:
        """Set an user's active status"""
        user.is_active = is_active

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def set_admin(self, user: User, is_admin: bool) -> User:
        """Set an user's admin status"""
        user.is_admin = is_admin

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def update_last_login_date(self, user: User) -> User:
        """Update an user's last login date"""
        user.last_login_at = datetime.now(UTC)

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)

        return user

    async def count(
        self,
        email: str | None = None,
        name: str | None = None,
        pseudo: str | None = None,
        is_active: bool | None = None,
        is_admin: bool | None = None,
    ) -> int:
        """Count all users"""
        query = select(func.count(User.id))
        query = self._apply_filters(
            query,
            email=email,
            name=name,
            pseudo=pseudo,
            is_active=is_active,
            is_admin=is_admin,
        )

        return await self.count_query(query)

    async def lock(self, user_id: int) -> None:
        """Lock the user's row until the end of the transaction."""
        await self.session.execute(select(User.id).where(User.id == user_id).with_for_update())

    def _apply_filters(
        self,
        query: Select,
        email: str | None = None,
        name: str | None = None,
        pseudo: str | None = None,
        is_active: bool | None = None,
        is_admin: bool | None = None,
    ) -> Select:
        """Helper qui applique les filtres sur une requête."""
        query = self._apply_filter_params(
            query,
            [
                FilterParams(column=User.email, value=email, op="ilike"),
                FilterParams(column=User.pseudo, value=pseudo, op="ilike"),
                FilterParams(column=User.is_active, value=is_active, op="eq"),
                FilterParams(column=User.is_admin, value=is_admin, op="eq"),
            ],
        )

        if name is not None:
            escaped_name = self.escape_ilike_value(name)
            query = query.where(
                User.first_name.ilike(f"%{escaped_name}%", escape="\\")
                | User.last_name.ilike(f"%{escaped_name}%", escape="\\")
            )
        return query
