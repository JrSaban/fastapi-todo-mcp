"""Configuration du serveur MCP, lue depuis l'environnement.

Deux modes de transport :
- `stdio` (par défaut) : lancé en local par l'agent, il appelle l'API avec un seul jeton `api`,
  celui de son utilisateur (`TODO_API_TOKEN`).
- `http` : serveur distant partagé par plusieurs agents. Chaque requête apporte le jeton `mcp`
  de son utilisateur, et le serveur s'identifie auprès de l'API avec sa clé de service
  (`MCP_SERVICE_KEY`), qu'aucune installation locale ne détient.
"""

from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

API_TOKEN_PREFIX = (
    "tda_"  # mirrors src.core.security.API_TOKEN_PREFIX, which mcp_server can't import
)


class Settings(BaseSettings):
    """MCP server settings, read from the environment only (never from .env)."""

    MCP_TRANSPORT: Literal["stdio", "http"] = "stdio"
    TODO_API_URL: str = "http://localhost:8000/api/v1"
    TODO_API_TOKEN: SecretStr | None = None
    MCP_SERVICE_KEY: SecretStr | None = None
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8001
    MCP_ALLOWED_HOSTS: list[str] = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    MCP_ALLOWED_ORIGINS: list[str] = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    MCP_TRUSTED_PROXIES: list[str] = []

    @field_validator("TODO_API_TOKEN")
    @classmethod
    def validate_todo_api_token(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and not v.get_secret_value().startswith(API_TOKEN_PREFIX):
            raise ValueError(f"TODO_API_TOKEN must start with {API_TOKEN_PREFIX}")
        return v

    @model_validator(mode="after")
    def check_transport_settings(self) -> Self:
        """Each transport gets exactly the secret it uses: a stdio server never holds the
        service key, and an HTTP server never holds a user's token of its own."""
        match self.MCP_TRANSPORT:
            case "stdio":
                if self.TODO_API_TOKEN is None:
                    raise ValueError("TODO_API_TOKEN is required for stdio transport")
                elif self.MCP_SERVICE_KEY is not None:
                    raise ValueError("MCP_SERVICE_KEY is not allowed for stdio transport")
            case "http":
                if (
                    self.MCP_SERVICE_KEY is None
                    or len(self.MCP_SERVICE_KEY.get_secret_value().encode()) < 32
                ):
                    raise ValueError(
                        "MCP_SERVICE_KEY is required for http transport "
                        "and must be at least 32 bytes"
                    )
                elif self.TODO_API_TOKEN is not None:
                    raise ValueError("TODO_API_TOKEN is not allowed for http transport")

        return self
