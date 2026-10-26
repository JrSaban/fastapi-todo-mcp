from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.core.broker import broker
from src.core.config import settings
from src.core.exceptions import AppBaseError
from src.core.logging import setup_logging
from src.core.middlewares import idempotency, log_requests, rate_limit
from src.modules.api_tokens.router import router as api_tokens_router
from src.modules.approvals.router import router as approvals_router
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


app.middleware("http")(idempotency)
app.middleware("http")(rate_limit)
app.middleware("http")(log_requests)

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
