"""
Point d'entrée du serveur MCP : `python -m mcp_server`, dans le mode choisi par `MCP_TRANSPORT`.

- `stdio` (par défaut) : lancé par Claude Code (voir `.mcp.json`). La sortie standard est le canal
  du protocole, donc rien d'autre ne doit y être écrit (pas de `print`), sous peine de casser la
  connexion.
- `http` : un serveur Uvicorn sur `MCP_HOST`:`MCP_PORT`, partagé par plusieurs agents.
"""

import uvicorn

from mcp_server.api_client import TodoApiClient
from mcp_server.config import Settings
from mcp_server.http_app import build_http_app
from mcp_server.server import build_mcp_server


def main() -> None:
    """Read the settings from the environment, then serve the tools over the chosen transport.

    Uvicorn's own proxy-header handling is off: it trusts 127.0.0.1 by default, a second source
    of truth next to `MCP_TRUSTED_PROXIES`.
    """
    settings = Settings()
    api = TodoApiClient(settings)
    server = build_mcp_server(api)

    if settings.MCP_TRANSPORT == "http":
        app = build_http_app(server, settings)
        uvicorn.run(app, host=settings.MCP_HOST, port=settings.MCP_PORT, proxy_headers=False)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
