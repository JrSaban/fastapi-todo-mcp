"""Routing & Controller Layer pour le domaine Users."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis

from src.core.database import DbSessionDep
from src.core.exceptions import InvalidPasswordError
from src.core.redis import get_redis_client
from src.core.schemas import LimitQuery, PaginatedResponse
from src.modules.api_tokens.router import ApiTokenServiceDep
from src.modules.auth.router import (
    AuthServiceDep,
    CurrentUserDep,
    SensitiveActionRateLimitDep,
    SessionUserDep,
    record_sensitive_action_failure,
)
from src.modules.users.models import User
from src.modules.users.repository import UserRepository
from src.modules.users.schemas import (
    UserActiveStatusUpdate,
    UserAdminStatusUpdate,
    UserCreate,
    UserEmailUpdate,
    UserPasswordUpdate,
    UserResponse,
    UserSelfEmailUpdate,
    UserUpdate,
)
from src.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_user_service(
    session: DbSessionDep,
) -> UserService:
    repository = UserRepository(session)
    return UserService(repository)


# Type alias pour injection propre et lisible (standard Python moderne)
UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un utilisateur",
    description="Crée un nouvel utilisateur et la persiste en base de données.",
)
async def create_user(
    service: UserServiceDep,
    data: UserCreate,
) -> User:
    return await service.create_user(data)


@router.get(
    "",
    response_model=PaginatedResponse[UserResponse],
    summary="Récupérer tous les utilisateurs",
    description="""
        Récupère tous les utilisateurs.
        Seuls les admins peuvent accéder à cette ressource.
    """,
)
async def list_users(
    service: UserServiceDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    email: Annotated[str | None, Query(min_length=2, description="Filtre sur l'email")] = None,
    name: Annotated[str | None, Query(min_length=2, description="Filtre sur le nom")] = None,
    pseudo: Annotated[str | None, Query(min_length=2, description="Filtre sur le pseudo")] = None,
    is_active: Annotated[bool | None, Query(description="Filtre sur le statut actif")] = None,
    is_admin: Annotated[bool | None, Query(description="Filtre sur le statut admin")] = None,
) -> PaginatedResponse[UserResponse]:
    users, total = await service.list_users(
        current_user=current_user,
        skip=skip,
        limit=limit,
        email=email,
        name=name,
        pseudo=pseudo,
        is_active=is_active,
        is_admin=is_admin,
    )
    return PaginatedResponse(items=users, total=total, skip=skip, limit=limit)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Afficher l'utilisateur connecté",
    description="Récupère les détails de l'utilisateur connecté.",
)
async def get_me(
    current_user: CurrentUserDep,
) -> User:
    return current_user


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Afficher un utilisateur",
    description="""
        Récupère les détails d'un utilisateur par son ID.
        Les admins peuvent recuperer n'importe quel utilisateur.
    """,
)
async def get_user(
    service: UserServiceDep,
    current_user: CurrentUserDep,
    user_id: int,
) -> User:
    return await service.get_user_or_404(current_user=current_user, entity_id=user_id)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Mettre à jour un utilisateur",
    description="""
        Mets à jour un utilisateur par son ID.
        Seul l'utilisateur concerné ou un admin peut modifier son profil.
    """,
)
async def update_user(
    service: UserServiceDep,
    current_user: CurrentUserDep,
    user_id: int,
    data: UserUpdate,
) -> User:
    return await service.update_user(current_user=current_user, entity_id=user_id, data=data)


@router.patch(
    "/me/email",
    response_model=UserResponse,
    summary="Changer l'email de l'utilisateur connecté",
    description="""
        Changer l'email de l'utilisateur connecté.
        Toutes les sessions de l'utilisateur seront révoquées.
    """,
)
async def update_email(
    service: UserServiceDep,
    auth_service: AuthServiceDep,
    current_user: SessionUserDep,
    sensitive_action_rate_limit: SensitiveActionRateLimitDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    data: UserSelfEmailUpdate,
) -> User:
    try:
        user = await service.update_email(
            current_user=current_user,
            data=data,
        )
    except InvalidPasswordError:
        await record_sensitive_action_failure(redis_client, current_user.id)
        raise

    await auth_service.revoke_session(current_user)
    return user


@router.patch(
    "/me/password",
    response_model=UserResponse,
    summary="Changer le mot de passe de l'utilisateur connecté",
    description="""
        Changer le mot de passe de l'utilisateur connecté.
        Toutes les sessions de l'utilisateur seront révoquées.
    """,
)
async def update_password(
    service: UserServiceDep,
    auth_service: AuthServiceDep,
    api_token_service: ApiTokenServiceDep,
    current_user: SessionUserDep,
    sensitive_action_rate_limit: SensitiveActionRateLimitDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    data: UserPasswordUpdate,
) -> User:
    try:
        user = await service.update_password(
            current_user=current_user,
            data=data,
        )
    except InvalidPasswordError:
        await record_sensitive_action_failure(redis_client, current_user.id)
        raise

    if data.revoke_api_tokens:
        await api_token_service.revoke_all(current_user)

    await auth_service.revoke_session(current_user)
    return user


@router.patch(
    "/{user_id}/email",
    response_model=UserResponse,
    summary="Changer l'email d'un utilisateur",
    description="""
        Changer l'email d'un utilisateur par son ID.
        Réservé aux admins, et jamais sur leur propre compte.
        Toutes les sessions de l'utilisateur seront révoquées.
    """,
)
async def set_user_email(
    service: UserServiceDep,
    auth_service: AuthServiceDep,
    current_user: CurrentUserDep,
    user_id: int,
    data: UserEmailUpdate,
) -> User:
    user = await service.set_user_email(current_user=current_user, entity_id=user_id, data=data)
    await auth_service.revoke_session(user)
    return user


@router.patch(
    "/{user_id}/active",
    response_model=UserResponse,
    summary="Activer ou désactiver un utilisateur",
    description="""
        Active ou désactive un utilisateur par son ID.
        Seul un admin peut activer ou désactiver un utilisateur.
    """,
)
async def set_user_active(
    service: UserServiceDep,
    current_user: CurrentUserDep,
    user_id: int,
    data: UserActiveStatusUpdate,
) -> User:
    return await service.set_user_active(
        current_user=current_user, entity_id=user_id, is_active=data.is_active
    )


@router.patch(
    "/{user_id}/admin",
    response_model=UserResponse,
    summary="Activer ou désactiver le statut admin d'un utilisateur",
    description="""
        Active ou désactive le statut admin d'un utilisateur par son ID.
        Seul un admin peut activer ou désactiver le statut admin d'un utilisateur.
    """,
)
async def set_user_admin(
    service: UserServiceDep,
    current_user: CurrentUserDep,
    user_id: int,
    data: UserAdminStatusUpdate,
) -> User:
    return await service.set_user_admin(
        current_user=current_user, entity_id=user_id, is_admin=data.is_admin
    )
