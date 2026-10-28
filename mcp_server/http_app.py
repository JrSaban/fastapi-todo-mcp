"""
L'application HTTP du serveur MCP distant, autour du transport « streamable HTTP » du SDK.

Sans état (aucune session MCP : chaque requête porte sa propre authentification), protégée
contre le DNS rebinding (`Host`/`Origin` autorisés), et fermée à toute requête sans jeton
d'API avant même le protocole MCP. L'IP de l'agent n'est lue dans `X-Forwarded-For` que
derrière un proxy de confiance.
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from mcp_server.config import API_TOKEN_PREFIX, Settings


async def require_api_token(request: Request, call_next):
    """Refuse any request without a `Bearer tda_...` header, `initialize` included, before it
    reaches the MCP protocol. Only the format is checked: the API decides if the token is valid.
    """
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")

    if scheme.lower() != "bearer" or not token.startswith(API_TOKEN_PREFIX):
        return JSONResponse(
            {"detail": "Missing or invalid API token."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)


def build_http_app(server: MCPServer, settings: Settings) -> Starlette:
    """Build the ASGI app serving `server` over HTTP.

    The transport security settings are always passed explicitly: the SDK only enables its
    DNS rebinding protection by itself for a localhost host, never for `0.0.0.0`.
    `ProxyHeadersMiddleware` wraps everything, so the client IP is rewritten before anything
    reads it.
    """
    app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.MCP_ALLOWED_HOSTS, allowed_origins=settings.MCP_ALLOWED_ORIGINS
        ),
    )
    app.add_middleware(BaseHTTPMiddleware, dispatch=require_api_token)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.MCP_TRUSTED_PROXIES)
    return app
