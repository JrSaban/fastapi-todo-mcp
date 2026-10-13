"""
Point d'entrée du serveur MCP : `python -m mcp_server`, lancé par Claude Code (voir `.mcp.json`).

Il communique en stdio : la sortie standard est le canal du protocole, donc rien d'autre ne doit
y être écrit (pas de `print`), sous peine de casser la connexion.
"""

from mcp_server.api_client import TodoApiClient
from mcp_server.config import Settings
from mcp_server.server import build_mcp_server


def main() -> None:
    """Read the settings from the environment, then serve the tools over stdio."""
    settings = Settings()
    api = TodoApiClient(settings)
    server = build_mcp_server(api)
    server.run("stdio")


if __name__ == "__main__":
    main()
