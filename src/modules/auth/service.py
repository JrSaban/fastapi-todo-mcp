"""Service Layer (Logique métier pour l'auth)."""

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from redis.asyncio import Redis

from src.core.config import settings
from src.core.exceptions import UnauthorizedError
from src.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from src.modules.auth.schemas import LoginRequest, TokenResponse
from src.modules.users.models import User
from src.modules.users.repository import UserRepository

_DUMMY_PASSWORD_HASH = hash_password("dummy_password")


class AuthService:
    def __init__(self, user_repository: UserRepository, redis_client: Redis) -> None:
        self.user_repository = user_repository
        self.redis_client = redis_client

    def _redis_key(self, key: Literal["user", "token", "session_start"], value: int | str) -> str:
        return f"refresh_token:{key}:{value}"

    async def _store_or_replace_refresh_token(self, user_id: int, refresh_token: str) -> None:
        """Store or replace the refresh token for a user."""
        hashed_refresh_token = hash_token(refresh_token)
        user_key = self._redis_key("user", user_id)
        new_token_key = self._redis_key("token", hashed_refresh_token)

        await self.redis_client.set(
            new_token_key, user_id, ex=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )
        old_token = cast(
            str | None,
            await self.redis_client.set(
                user_key,
                hashed_refresh_token,
                ex=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
                get=True,
            ),
        )

        if old_token is not None:
            await self.redis_client.delete(self._redis_key("token", old_token))

    async def revoke_session(self, user: User) -> None:
        """Revoke the user's sessions."""
        user_key = self._redis_key("user", user.id)
        old_token = cast(str | None, await self.redis_client.getdel(user_key))

        await self.redis_client.delete(self._redis_key("session_start", user.id))

        if old_token is not None:
            await self.redis_client.delete(self._redis_key("token", old_token))

    async def login(self, data: LoginRequest) -> TokenResponse:
        """Login a user."""
        user = await self.user_repository.get_by_email(data.email)

        hashed_password = user.hashed_password if user is not None else _DUMMY_PASSWORD_HASH
        password_valid = verify_password(data.password, hashed_password)

        if user is None or not password_valid:
            raise UnauthorizedError("Email ou mot de passe incorrect")
        if not user.is_active:
            raise UnauthorizedError("Votre compte n'est pas actif")

        await self.user_repository.update_last_login_date(user)

        access_token = create_access_token({"sub": str(user.id)})
        refresh_token = generate_refresh_token()

        session_start_key = self._redis_key("session_start", user.id)
        await self.redis_client.set(
            session_start_key,
            datetime.now(UTC).isoformat(),
            ex=timedelta(days=settings.REFRESH_TOKEN_ABSOLUTE_MAX_DAYS),
        )

        await self._store_or_replace_refresh_token(user.id, refresh_token)

        return TokenResponse(access_token=access_token, refresh_token=refresh_token)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        """Refresh a user token."""
        hashed_refresh_token = hash_token(refresh_token)
        user_id = cast(
            str | None,
            await self.redis_client.getdel(self._redis_key("token", hashed_refresh_token)),
        )
        if user_id is None:
            raise UnauthorizedError("Refresh token invalide ou expiré")

        session_valid = await self.redis_client.get(self._redis_key("session_start", user_id))
        if session_valid is None:
            raise UnauthorizedError("Refresh token invalide ou expiré")

        user = await self.user_repository.get_by_id(int(user_id))
        if user is None or not user.is_active:
            raise UnauthorizedError("Refresh token invalide ou expiré")

        access_token = create_access_token({"sub": str(user.id)})
        new_refresh_token = generate_refresh_token()

        await self._store_or_replace_refresh_token(user.id, new_refresh_token)

        return TokenResponse(access_token=access_token, refresh_token=new_refresh_token)

    async def logout(self, user: User) -> None:
        """Logout a user."""
        await self.revoke_session(user)
