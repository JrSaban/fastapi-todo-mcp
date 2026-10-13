"""Routing & Controller Layer pour le domaine API tokens."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from src.core.database import DbSessionDep
from src.core.exceptions import InvalidPasswordError
from src.core.redis import get_redis_client
from src.modules.api_tokens.models import ApiToken
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.api_tokens.schemas import ApiTokenCreate, ApiTokenCreatedResponse, ApiTokenResponse
from src.modules.api_tokens.service import ApiTokenService
from src.modules.auth.router import (
    CurrentUserDep,
    SensitiveActionRateLimitDep,
    SessionUserDep,
    record_sensitive_action_failure,
)
from src.modules.users.repository import UserRepository

router = APIRouter(prefix="/api-tokens", tags=["API tokens"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_api_token_service(session: DbSessionDep) -> ApiTokenService:
    api_token_repository = ApiTokenRepository(session)
    user_repository = UserRepository(session)
    return ApiTokenService(repository=api_token_repository, user_repository=user_repository)


# Type alias pour injection propre et lisible (standard Python moderne)
ApiTokenServiceDep = Annotated[ApiTokenService, Depends(get_api_token_service)]


@router.post(
    "",
    response_model=ApiTokenCreatedResponse,
    status_code=201,
    summary="Création d'un token API",
    description="Création d'un token API.",
)
async def create_api_token(
    service: ApiTokenServiceDep,
    current_user: SessionUserDep,
    sensitive_action_rate_limit: SensitiveActionRateLimitDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    data: ApiTokenCreate,
) -> ApiTokenCreatedResponse:
    try:
        api_token, plain_token = await service.create_token(current_user, data)
    except InvalidPasswordError:
        await record_sensitive_action_failure(redis_client, current_user.id)
        raise

    return ApiTokenCreatedResponse(
        **ApiTokenResponse.model_validate(api_token).model_dump(),
        token=plain_token,
    )


@router.get(
    "",
    response_model=list[ApiTokenResponse],
    summary="Liste des tokens API",
    description="Liste des tokens API.",
)
async def list_api_tokens(
    service: ApiTokenServiceDep,
    current_user: CurrentUserDep,
) -> Sequence[ApiToken]:
    return await service.list_tokens(current_user)


@router.delete(
    "/{token_id}",
    response_model=None,
    status_code=204,
    summary="Suppression d'un token API",
    description="Suppression d'un token API.",
)
async def delete_api_token(
    service: ApiTokenServiceDep,
    current_user: SessionUserDep,
    token_id: int,
) -> None:
    await service.revoke_token(current_user, token_id)
