"""Service Layer (Logique métier pour les Users)."""

from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictError, ForbiddenError, InvalidPasswordError, NotFoundError
from src.core.security import hash_password, verify_password
from src.modules.audit.changes import changed_fields
from src.modules.audit.service import AuditLogService
from src.modules.users.models import User
from src.modules.users.repository import UserRepository
from src.modules.users.schemas import (
    UserCreate,
    UserEmailUpdate,
    UserPasswordUpdate,
    UserSelfEmailUpdate,
    UserUpdate,
)


class UserService:
    def __init__(self, repository: UserRepository, audit_service: AuditLogService) -> None:
        self.repository = repository
        self.audit = audit_service

    async def list_users(
        self,
        current_user: User,
        skip: int = 0,
        limit: int = 25,
        email: str | None = None,
        name: str | None = None,
        pseudo: str | None = None,
        is_active: bool | None = None,
        is_admin: bool | None = None,
    ) -> tuple[Sequence[User], int]:
        """Get all users with pagination."""
        if not current_user.is_admin:
            raise ForbiddenError("Vous n'avez pas l'autorisation de lister les utilisateurs.")

        users = await self.repository.get_all(
            skip=skip,
            limit=limit,
            email=email,
            name=name,
            pseudo=pseudo,
            is_active=is_active,
            is_admin=is_admin,
        )
        total = await self.repository.count(
            email=email,
            name=name,
            pseudo=pseudo,
            is_active=is_active,
            is_admin=is_admin,
        )

        return (users, total)

    async def get_user_or_404(self, current_user: User, entity_id: int) -> User:
        """Get a user or raise an HTTP 404 exception."""
        if not current_user.is_admin and current_user.id != entity_id:
            raise ForbiddenError("Vous n'avez pas l'autorisation d'accéder à ce user.")

        user = await self.repository.get_by_id(entity_id)
        if not user:
            raise NotFoundError(f"User avec l'ID {entity_id} introuvable.")
        return user

    async def create_user(self, data: UserCreate) -> User:
        """Create a new user."""
        hashed_password = hash_password(data.password)
        try:
            user = await self.repository.create(data, hashed_password)
            await self.audit.log_created(entity=user)
            return user
        except IntegrityError as e:
            if "uq_users_email_lower" in str(e):
                raise ConflictError(f"Un utilisateur avec l'email {data.email} existe déjà.") from e
            if "uq_users_pseudo_lower" in str(e):
                raise ConflictError(
                    f"Un utilisateur avec le pseudo {data.pseudo} existe déjà."
                ) from e
            raise

    async def update_user(self, current_user: User, entity_id: int, data: UserUpdate) -> User:
        """Update an existing user."""
        user = await self.get_user_or_404(current_user, entity_id)

        changes = changed_fields(user, data.model_dump(exclude_unset=True))

        try:
            updated = await self.repository.update(user, data)
        except IntegrityError as e:
            if "uq_users_pseudo_lower" in str(e):
                raise ConflictError(
                    f"Un utilisateur avec le pseudo {data.pseudo} existe déjà."
                ) from e
            raise

        await self.audit.log_updated(entity=updated, changed_fields=changes)
        return updated

    async def update_email(self, current_user: User, data: UserSelfEmailUpdate) -> User:
        """Update email of a user."""
        if not verify_password(data.current_password, current_user.hashed_password):
            raise InvalidPasswordError("Le mot de passe actuel ne correspond pas")

        try:
            user = await self.repository.update_email(current_user, data.new_email)
            await self.audit.log_updated(entity=user, changed_fields=["email"])
            return user
        except IntegrityError as e:
            if "uq_users_email_lower" in str(e):
                raise ConflictError(
                    f"Un utilisateur avec l'email {data.new_email} existe déjà."
                ) from e
            raise

    async def update_password(self, current_user: User, data: UserPasswordUpdate) -> User:
        """Update password of a user."""
        if not verify_password(data.old_password, current_user.hashed_password):
            raise InvalidPasswordError("L'ancien mot de passe ne correspond pas")

        hashed_password = hash_password(data.new_password)
        user = await self.repository.update_password(current_user, hashed_password)
        await self.audit.log_updated(entity=user, changed_fields=["password"])
        return user

    async def set_user_email(
        self, current_user: User, entity_id: int, data: UserEmailUpdate
    ) -> User:
        """Set an user's email."""
        if not current_user.is_admin or current_user.id == entity_id:
            raise ForbiddenError("Vous n'avez pas l'autorisation de modifier cet utilisateur.")

        user = await self.get_user_or_404(current_user, entity_id)

        try:
            user = await self.repository.update_email(user, data.new_email)
            await self.audit.log_updated(entity=user, changed_fields=["email"])
            return user
        except IntegrityError as e:
            if "uq_users_email_lower" in str(e):
                raise ConflictError(
                    f"Un utilisateur avec l'email {data.new_email} existe déjà."
                ) from e
            raise

    async def set_user_active(self, current_user: User, entity_id: int, is_active: bool) -> User:
        """Set an user's active status."""
        if not current_user.is_admin:
            raise ForbiddenError("Vous n'avez pas l'autorisation de modifier cet utilisateur.")

        changes = []
        user = await self.get_user_or_404(current_user, entity_id)

        if user.is_active != is_active:
            changes.append("is_active")

        if not is_active and user.is_admin:
            total_users_admins = await self.repository.count(is_admin=True, is_active=True)
            if total_users_admins == 1:
                raise ForbiddenError("Vous ne pouvez pas désactiver le dernier admin.")

        user = await self.repository.set_active(user, is_active)

        if changes:
            await self.audit.log_updated(entity=user, changed_fields=changes)
        return user

    async def set_user_admin(self, current_user: User, entity_id: int, is_admin: bool) -> User:
        """Set an user's admin status."""
        if not current_user.is_admin:
            raise ForbiddenError("Vous n'avez pas l'autorisation de modifier cet utilisateur.")

        changes = []
        user = await self.get_user_or_404(current_user, entity_id)

        if user.is_admin != is_admin:
            changes.append("is_admin")

        if not is_admin and user.is_admin:
            total_users_admins = await self.repository.count(is_admin=True, is_active=True)
            if total_users_admins == 1:
                raise ForbiddenError(
                    "Vous ne pouvez pas retirer le statut d'admin au dernier admin."
                )

        user = await self.repository.set_admin(user, is_admin)

        if changes:
            await self.audit.log_updated(entity=user, changed_fields=changes)
        return user
