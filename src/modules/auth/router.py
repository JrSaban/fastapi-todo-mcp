"""Routing & Controller Layer pour le domaine Auth."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import DbSessionDep
from src.core.exceptions import (
    ForbiddenError,
    InvalidPasswordError,
    TooManyRequestsError,
    UnauthorizedError,
)
from src.core.rate_limit import (
    release_attempt,
    reserve_attempt,
)
from src.core.redis import get_redis_client
from src.core.security import (
    API_TOKEN_PREFIX,
    MCP_SERVICE_KEY_HEADER,
    decode_access_token,
    expected_api_token_usage,
    hash_token,
)
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.audit.context import Actor, current_actor
from src.modules.audit.models import AuditAuthMethod
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

    current_actor.set(
        Actor(user_id=user.id, auth_method=AuditAuthMethod.SESSION, api_token_id=None)
    )

    return user


async def _user_from_api_token(
    *, mcp_key_header: str | None, token: str, session: AsyncSession
) -> User:
    """Get user from API token."""

    if (usage := expected_api_token_usage(mcp_key_header)) is None:
        raise UnauthorizedError("Token invalide ou expiré.")

    api_token_repository = ApiTokenRepository(session)
    now = datetime.now(UTC)
    api_token = await api_token_repository.get_active_by_hash(usage, hash_token(token), now)
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

    current_actor.set(
        Actor(user_id=user.id, auth_method=AuditAuthMethod.API_TOKEN, api_token_id=api_token.id)
    )

    return user


# Dependency pour injecter l'utilisateur connecté
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    session: DbSessionDep,
    request: Request,
) -> User:
    """Get current user."""
    token = credentials.credentials
    if token.startswith(API_TOKEN_PREFIX):
        mcp_service_header = request.headers.get(MCP_SERVICE_KEY_HEADER)
        return await _user_from_api_token(
            mcp_key_header=mcp_service_header, token=token, session=session
        )

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
) -> AsyncGenerator[None, None]:
    ip_key, email_key = _rate_limit_redis_keys(request, data.email)

    retry_after = await reserve_attempt(
        redis_client,
        [ip_key, email_key],
        settings.LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        settings.LOGIN_RATE_LIMIT_WINDOW_MINUTES,
    )

    if retry_after is not None:
        raise TooManyRequestsError(
            "Trop de tentatives, veuillez réessayer plus tard.",
            headers={"Retry-After": str(retry_after)},
        )

    failed_attempt: bool = False

    try:
        yield
    except UnauthorizedError:
        failed_attempt = True
        raise
    finally:
        if not failed_attempt:
            await release_attempt(redis_client, [ip_key, email_key])


def is_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
) -> bool:
    """Check if the current user is using an API token."""
    return credentials is not None and credentials.credentials.startswith(API_TOKEN_PREFIX)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
# Type alias pour injection propre et lisible (standard Python moderne)
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
RateLimitLoginDep = Annotated[None, Depends(check_login_rate_limit)]
IsApiTokenDep = Annotated[bool, Depends(is_api_token)]


async def get_session_user(
    current_user: CurrentUserDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(optional_bearer)],
) -> User:
    """Get session user (not API token)."""
    if is_api_token(credentials):
        raise ForbiddenError("Vous ne pouvez pas utiliser un token API sur cette ressource.")

    return current_user


SessionUserDep = Annotated[User, Depends(get_session_user)]


async def check_sensitive_action_rate_limit(
    current_user: CurrentUserDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> AsyncGenerator[None, None]:
    key = sensitive_action_rate_limit_key(current_user.id)
    retry_after = await reserve_attempt(
        redis_client,
        [key],
        settings.SENSITIVE_RATE_LIMIT_MAX_ATTEMPTS,
        settings.SENSITIVE_RATE_LIMIT_WINDOW_MINUTES,
    )
    if retry_after is not None:
        raise TooManyRequestsError(
            "Trop de tentatives, veuillez réessayer plus tard.",
            headers={"Retry-After": str(retry_after)},
        )

    failed_attempt: bool = False

    try:
        yield
    except InvalidPasswordError:
        failed_attempt = True
        raise
    finally:
        if not failed_attempt:
            await release_attempt(redis_client, [key])


SensitiveActionRateLimitDep = Annotated[None, Depends(check_sensitive_action_rate_limit)]


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login un utilisateur",
    description="Connecte un utilisateur et retourne un token.",
)
async def login(
    service: AuthServiceDep,
    rate_limit: RateLimitLoginDep,
    data: LoginRequest,
) -> TokenResponse:
    return await service.login(data)


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
    current_user: SessionUserDep,
) -> None:
    return await service.logout(current_user)
