import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import jwt
import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.core.broker import broker
from src.core.config import settings
from src.core.database import async_session_factory
from src.core.exceptions import (
    AppBaseError,
)
from src.core.idempotency import (
    claim_idempotency_key,
    get_idempotency_record,
    hash_request,
    idempotency_redis_key,
    release_idempotency_key,
    save_idempotent_response,
)
from src.core.logging import setup_logging
from src.core.rate_limit import increment_rate_limit, seconds_until_reset
from src.core.redis import get_redis_client
from src.core.security import API_TOKEN_PREFIX, decode_access_token, hash_token
from src.modules.api_tokens.repository import ApiTokenRepository
from src.modules.api_tokens.router import router as api_tokens_router
from src.modules.approvals.router import router as approvals_router
from src.modules.audit.context import current_actor
from src.modules.audit.router import router as audit_router
from src.modules.auth.router import router as auth_router
from src.modules.categories.router import router as categories_router
from src.modules.jobs.router import router as jobs_router
from src.modules.maintenance.router import router as maintenance_router
from src.modules.todos.router import router as todos_router
from src.modules.users.router import router as users_router

"""
Point d'entrée principal de l'application.
Équivalent conceptuel de bootstrap/app.php + public/index.php dans Laravel.
"""


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the task broker with the app and shut it down on exit."""
    await broker.startup()
    yield
    await broker.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    description="API REST moderne construite avec FastAPI et Clean Architecture",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    lifespan=lifespan,
)

# Enregistrement des routes de l'API avec préfixe de version (/api/v1)
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(todos_router, prefix=settings.API_V1_STR)
app.include_router(categories_router, prefix=settings.API_V1_STR)
app.include_router(users_router, prefix=settings.API_V1_STR)
app.include_router(maintenance_router, prefix=settings.API_V1_STR)
app.include_router(jobs_router, prefix=settings.API_V1_STR)
app.include_router(api_tokens_router, prefix=settings.API_V1_STR)
app.include_router(approvals_router, prefix=settings.API_V1_STR)
app.include_router(audit_router, prefix=settings.API_V1_STR)


logger = structlog.get_logger()
REQUEST_ID_HEADER = "X-Request-ID"
IDEMPOTENT_ROUTES = frozenset(
    [
        f"{settings.API_V1_STR}/todos",
        f"{settings.API_V1_STR}/categories",
        f"{settings.API_V1_STR}/todos/delete-completed",
    ]
)


def _get_request_id(request: Request) -> str:
    """Extract request ID from header or generate a new one."""
    incoming = request.headers.get(REQUEST_ID_HEADER)
    if incoming and len(incoming) <= 64:
        return incoming
    return str(uuid.uuid4())


async def _rate_limit_identity(request: Request) -> str:
    """User id from a valid access token, else the client IP."""
    if user_id := await _request_user_id(request):
        return f"user:{user_id}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _user_id_from_access_token(token: str) -> str | None:
    """Extract user ID from access token."""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        payload = {}
    if user_id := payload.get("sub"):
        return user_id

    return None


async def _user_id_from_api_token(token: str) -> str | None:
    """Extract user ID from API token."""
    try:
        async with async_session_factory() as session:
            owner_id = await ApiTokenRepository(session).get_usable_owner_id(
                token_hash=hash_token(token),
                now=datetime.now(UTC),
            )
    except (SQLAlchemyError, OSError):
        logger.warning("database_unavailable", operation="api_token_identity", exc_info=True)
        return None

    return str(owner_id) if owner_id is not None else None


async def _request_user_id(request: Request) -> str | None:
    """Extract user ID from the request."""
    if hasattr(request.state, "user_id"):
        return request.state.user_id

    scheme, token = get_authorization_scheme_param(request.headers.get("Authorization"))

    if scheme.lower() != "bearer" or not token:
        request.state.auth_method = None
        return None

    if token.startswith(API_TOKEN_PREFIX):
        request.state.auth_method = "api_token"
        user_id = await _user_id_from_api_token(token)
    else:
        request.state.auth_method = "access_token"
        user_id = _user_id_from_access_token(token)

    request.state.user_id = user_id
    return user_id


def _is_idempotent_route(request: Request) -> bool:
    """Whether the route accepts an Idempotency-Key."""
    return request.method == "POST" and request.url.path in IDEMPOTENT_ROUTES


def _get_idempotency_key(request: Request) -> str | None:
    """Extract idempotency key from header."""
    idempotency_key = request.headers.get("Idempotency-Key", "")
    return idempotency_key if idempotency_key and len(idempotency_key) <= 255 else None


@app.middleware("http")
async def idempotency(request: Request, call_next):
    """Middleware for idempotency for idempotent requests."""
    if not _is_idempotent_route(request):
        return await call_next(request)

    if not (user_id := await _request_user_id(request)):
        return await call_next(request)

    if request.headers.get("Idempotency-Key") is None:
        if request.state.auth_method == "api_token":
            return JSONResponse(
                status_code=400,
                content={"detail": "La clé d'idempotence est obligatoire avec un token API."},
            )

        return await call_next(request)

    if not (idempotency_key := _get_idempotency_key(request)):
        return JSONResponse(
            status_code=400,
            content={"detail": "Clé d'idempotence invalide."},
        )

    redis_key = idempotency_redis_key(user_id=user_id, idempotency_key=idempotency_key)
    request_hash = hash_request(
        method=request.method, path=request.url.path, body=await request.body()
    )
    record = None

    try:
        is_claimed = await claim_idempotency_key(
            get_redis_client(), key=redis_key, request_hash=request_hash
        )
        if not is_claimed:
            record = await get_idempotency_record(get_redis_client(), key=redis_key)
    except RedisError:
        logger.error("redis_unavailable", operation="idempotency_check", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"detail": "Le service de gestion des requêtes est indisponible."},
        )

    if not is_claimed:
        if record:
            if record["request_hash"] != request_hash:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": (
                            "Cette clé d'idempotence a déjà été utilisée pour "
                            "une requête différente."
                        )
                    },
                )
            elif record["status"] == "in_progress":
                try:
                    retry_after = await seconds_until_reset(get_redis_client(), redis_key)
                except RedisError:
                    logger.error(
                        "redis_unavailable", operation="seconds_until_reset", exc_info=True
                    )
                    return JSONResponse(
                        status_code=503,
                        content={"detail": "Le service de gestion des requêtes est indisponible."},
                    )

                return JSONResponse(
                    status_code=409,
                    headers={"Retry-After": str(retry_after)},
                    content={"detail": "Une requête similaire est en cours de traitement"},
                )
            else:
                # record["status"] == "done"
                return Response(
                    status_code=record["response_status"],
                    content=record["response_body"],
                    media_type="application/json",
                    headers={"Idempotent-Replayed": "true"},
                )
        else:
            return JSONResponse(
                status_code=409,
                content={"detail": "La requête n'a pas pu être traitée. Veuillez réessayer."},
            )

    try:
        response = await call_next(request)
    except Exception:
        await release_idempotency_key(get_redis_client(), redis_key)
        raise

    body_parts = [chunk async for chunk in response.body_iterator]
    response_body = b"".join(body_parts)

    if 200 <= response.status_code < 300:
        await save_idempotent_response(
            redis_client=get_redis_client(),
            key=redis_key,
            request_hash=request_hash,
            response_status=response.status_code,
            response_body=response_body.decode(),
        )
    else:
        await release_idempotency_key(get_redis_client(), redis_key)

    return Response(
        content=response_body, status_code=response.status_code, headers=dict(response.headers)
    )


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    identity = await _rate_limit_identity(request)
    key = f"rate_limit:general:{identity}"

    try:
        attempts = await increment_rate_limit(
            get_redis_client(), [key], settings.GENERAL_RATE_LIMIT_WINDOW_MINUTES
        )
        if attempts[key] > settings.GENERAL_RATE_LIMIT_MAX_REQUESTS:
            retry_after = await seconds_until_reset(get_redis_client(), key)
            return JSONResponse(
                status_code=429,
                content={"detail": "Trop de requêtes, veuillez réessayer plus tard."},
                headers={"Retry-After": str(retry_after)},
            )
    except RedisError:
        logger.warning("redis_unavailable", operation="rate_limit_middleware", exc_info=True)

    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    current_actor.set(None)
    request_id = _get_request_id(request)
    structlog.contextvars.bind_contextvars(request_id=request_id)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration_ms, 1),
    )
    return response


# Must stay the last middleware declared, so it wraps the others: the rate limits read
# request.client.host, which it rewrites from X-Forwarded-For for trusted proxies only.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.TRUSTED_PROXIES)


@app.exception_handler(AppBaseError)
async def app_error_handler(request: Request, exc: AppBaseError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)},
        headers=exc.headers,
    )


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    """
    Endpoint de vérification de l'état de l'API.
    Utilisé par Docker / Kubernetes / Load Balancer pour les healthchecks.
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
    }
