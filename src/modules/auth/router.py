"""Routing & Controller Layer pour le domaine Auth."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import DbSessionDep
from src.core.exceptions import ForbiddenError, TooManyRequestsError, UnauthorizedError
from src.core.rate_limit import get_retry_after, increment_rate_limit
from src.core.redis import get_redis_client
from src.core.security import API_TOKEN_PREFIX, decode_access_token, hash_token
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.auth.schemas import LoginRequest, RefreshTokenRequest, TokenResponse
from src.modules.auth.service import AuthService
from src.modules.users.models import User
from src.modules.users.repository import UserRepository

router = APIRouter(tags=["Auth"])

bearer_scheme = HTTPBearer()
optional_bearer = HTTPBearer(auto_error=False)


def sensitive_action_rate_limit_key(user_id: int) -> str:
    """Redis key of the wrong-password counter shared by all sensitive actions."""
    return f"rate_limit:sensitive_actions:user:{user_id}"


def _rate_limit_redis_keys(request: Request, email: str) -> tuple[str, str]:
    """Get the rate limit keys for a request and email."""
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"rate_limit:login:ip:{client_ip}"
    email_key = f"rate_limit:login:email:{email}"

    return ip_key, email_key


async def _user_from_access_token(token: str, session: AsyncSession) -> User:
    """Get user from access token (JWT)."""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as jwt_error:
        raise UnauthorizedError("Token invalide ou expiré.") from jwt_error

    user_id = payload.get("sub")
    if user_id is None:
        raise UnauthorizedError("Token invalide.")

    user_repository = UserRepository(session)
    user = await user_repository.get_by_id(int(user_id))
    if user is None or not user.is_active:
        raise UnauthorizedError("Utilisateur introuvable ou inactif.")

    return user


async def _user_from_api_token(token: str, session: AsyncSession) -> User:
    """Get user from API token."""
    api_token_repository = ApiTokenRepository(session)
    now = datetime.now(UTC)
    api_token = await api_token_repository.get_active_by_hash(hash_token(token), now)
    if api_token is None:
        raise UnauthorizedError("Token invalide ou expiré.")

    user_repository = UserRepository(session)
    user = await user_repository.get_by_id(api_token.owner_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Utilisateur introuvable ou inactif.")

    if user.is_admin:
        raise ForbiddenError("Vous ne pouvez pas utiliser un token API en tant qu'admin.")

    older_than = now - timedelta(hours=1)
    await api_token_repository.touch_last_used(api_token.id, now, older_than)

    return user


# Dependency pour injecter l'utilisateur connecté
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    session: DbSessionDep,
) -> User:
    """Get current user."""
    token = credentials.credentials
    if token.startswith(API_TOKEN_PREFIX):
        return await _user_from_api_token(token, session)

    return await _user_from_access_token(token, session)


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_auth_service(
    session: DbSessionDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> AuthService:
    user_repository = UserRepository(session)
    return AuthService(user_repository=user_repository, redis_client=redis_client)


async def check_login_rate_limit(
    request: Request,
    data: LoginRequest,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> None:
    ip_key, email_key = _rate_limit_redis_keys(request, data.email)

    retry_after = await get_retry_after(
        redis_client, [ip_key, email_key], settings.LOGIN_RATE_LIMIT_MAX_ATTEMPTS
    )
    if retry_after is not None:
        raise TooManyRequestsError(
            "Trop de tentatives, veuillez réessayer plus tard.",
            headers={"Retry-After": str(retry_after)},
        )


async def record_sensitive_action_failure(redis_client: Redis, user_id: int) -> None:
    """Count one wrong current password toward the user's sensitive-action limit."""
    key = sensitive_action_rate_limit_key(user_id)
    await increment_rate_limit(redis_client, [key], settings.SENSITIVE_RATE_LIMIT_WINDOW_MINUTES)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
# Type alias pour injection propre et lisible (standard Python moderne)
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
RateLimitLoginDep = Annotated[None, Depends(check_login_rate_limit)]


async def get_session_user(
    current_user: CurrentUserDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
) -> User:
    """Get session user (not API token)."""
    if credentials is not None and credentials.credentials.startswith(API_TOKEN_PREFIX):
        raise ForbiddenError("Vous ne pouvez pas utiliser un token API sur cette ressource.")

    return current_user


SessionUserDep = Annotated[User, Depends(get_session_user)]


async def check_sensitive_action_rate_limit(
    current_user: CurrentUserDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> None:
    key = sensitive_action_rate_limit_key(current_user.id)
    retry_after = await get_retry_after(
        redis_client, [key], settings.SENSITIVE_RATE_LIMIT_MAX_ATTEMPTS
    )
    if retry_after is not None:
        raise TooManyRequestsError(
            "Trop de tentatives, veuillez réessayer plus tard.",
            headers={"Retry-After": str(retry_after)},
        )


SensitiveActionRateLimitDep = Annotated[None, Depends(check_sensitive_action_rate_limit)]


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login un utilisateur",
    description="Connecte un utilisateur et retourne un token.",
)
async def login(
    service: AuthServiceDep,
    request: Request,
    rate_limit: RateLimitLoginDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    data: LoginRequest,
) -> TokenResponse:
    try:
        return await service.login(data)
    except UnauthorizedError:
        ip_key, email_key = _rate_limit_redis_keys(request, data.email)
        await increment_rate_limit(
            redis_client, [ip_key, email_key], settings.LOGIN_RATE_LIMIT_WINDOW_MINUTES
        )
        raise


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh un token",
    description="Refresh un token.",
)
async def refresh(
    service: AuthServiceDep,
    data: RefreshTokenRequest,
) -> TokenResponse:
    return await service.refresh(data.refresh_token)


@router.post(
    "/logout",
    response_model=None,
    status_code=204,
    summary="Logout un utilisateur",
    description="Déconnecte un utilisateur.",
)
async def logout(
    service: AuthServiceDep,
    current_user: CurrentUserDep,
) -> None:
    return await service.logout(current_user)
