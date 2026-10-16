"""Service Layer (Logique métier pour l'ApiToken)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.core.config import settings
from src.core.exceptions import ConflictError, ForbiddenError, InvalidPasswordError, NotFoundError
from src.core.security import (
    generate_api_token,
    hash_token,
    verify_password,
)
from src.modules.api_tokens.models import ApiToken
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.api_tokens.schemas import ApiTokenCreate
from src.modules.audit.models import AuditAction, AuditResourceType
from src.modules.audit.service import AuditLogService
from src.modules.users.models import User
from src.modules.users.repository import UserRepository


class ApiTokenService:
    def __init__(
        self,
        repository: ApiTokenRepository,
        user_repository: UserRepository,
        audit_service: AuditLogService,
    ) -> None:
        self.repository = repository
        self.user_repository = user_repository
        self.audit = audit_service

    async def create_token(self, current_user: User, data: ApiTokenCreate) -> tuple[ApiToken, str]:
        """Create a new API token for the current user."""
        if current_user.is_admin:
            raise ForbiddenError("Vous ne pouvez pas utiliser un token API en tant qu'admin.")

        if not verify_password(data.current_password, current_user.hashed_password):
            raise InvalidPasswordError("Le mot de passe actuel ne correspond pas")

        await self.user_repository.lock(current_user.id)

        now = datetime.now(UTC)
        active_tokens = await self.repository.count_active(current_user.id, now)
        if active_tokens >= settings.API_TOKEN_MAX_ACTIVE:
            raise ConflictError("Vous avez atteint le nombre maximum de tokens actifs")

        plain_token = generate_api_token()
        expires_at = now + timedelta(days=data.expires_in_days) if data.expires_in_days else None
        new_api_token = await self.repository.create(
            owner_id=current_user.id,
            name=data.name,
            token_hash=hash_token(plain_token),
            expires_at=expires_at,
        )

        await self.audit.log_created(new_api_token)
        return new_api_token, plain_token

    async def list_tokens(self, current_user: User) -> Sequence[ApiToken]:
        """List all API tokens for the current user."""
        return await self.repository.list_for_owner(current_user.id)

    async def revoke_token(self, current_user: User, token_id: int) -> None:
        """Revoke an API token for the current user."""
        token = await self.repository.get_owned(token_id=token_id, owner_id=current_user.id)
        if token is None:
            raise NotFoundError("Token introuvable.")

        await self.repository.delete(token)
        await self.audit.log_deleted(token)

    async def revoke_all(self, current_user: User) -> int:
        """Revoke all API tokens for the current user."""
        deleted_ids = await self.repository.delete_all_for_owner(current_user.id)

        for token_id in deleted_ids:
            await self.audit.record(
                resource_type=AuditResourceType.API_TOKEN,
                resource_id=token_id,
                resource_owner_id=current_user.id,
                action=AuditAction.DELETED,
            )

        return len(deleted_ids)

    async def prune_expired(self) -> dict[str, int]:
        """Prune expired API tokens."""
        cutoff_date = datetime.now(UTC) - timedelta(days=settings.API_TOKEN_EXPIRED_RETENTION_DAYS)
        tokens_deleted = await self.repository.delete_expired_before(cutoff_date)

        return {
            "api_tokens": tokens_deleted,
        }
