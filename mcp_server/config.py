"""Configuration du serveur MCP, lue depuis l'environnement :
l'adresse de l'API et le jeton d'API avec lequel il l'appelle."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings

API_TOKEN_PREFIX = (
    "tda_"  # mirrors src.core.security.API_TOKEN_PREFIX, which mcp_server can't import
)


class Settings(BaseSettings):
    """MCP server settings, read from the environment only (never from .env)."""

    TODO_API_URL: str = "http://localhost:8000/api/v1"
    TODO_API_TOKEN: SecretStr

    @field_validator("TODO_API_TOKEN")
    @classmethod
    def validate_todo_api_token(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().startswith(API_TOKEN_PREFIX):
            raise ValueError(f"TODO_API_TOKEN must start with {API_TOKEN_PREFIX}")
        return v
