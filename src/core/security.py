"""Security utilities."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from src.core.config import settings
from src.modules.api_tokens.models import ApiTokenUsage

_password_hasher = PasswordHasher()

API_TOKEN_PREFIX: str = "tda_"
MCP_SERVICE_KEY_HEADER: str = "X-MCP-Service-Key"


def hash_password(password: str) -> str:
    """Hash a plain text password."""
    return _password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a hashed password."""
    try:
        _password_hasher.verify(hashed_password, password)
        return True
    except VerifyMismatchError:
        return False


def create_access_token(data: dict) -> str:
    """Create a JWT access token, valid for ACCESS_TOKEN_EXPIRE_MINUTES minutes."""
    expire = datetime.now(UTC)
    expire += timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode a JWT access token and return its payload."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_refresh_token() -> str:
    """Generate a refresh token."""
    return secrets.token_urlsafe(32)


def generate_api_token() -> str:
    """Generate a API token."""
    return f"{API_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Hash a plain text token."""
    return hashlib.sha256(token.encode()).hexdigest()


def expected_api_token_usage(service_key: str | None) -> ApiTokenUsage | None:
    """Return the expected usage of an API token based on the service key."""
    if not str(service_key).strip() or service_key is None:
        return ApiTokenUsage.API

    if settings.MCP_SERVICE_KEY is None:
        return None

    mcp_service_key = settings.MCP_SERVICE_KEY.get_secret_value()

    if secrets.compare_digest(service_key.encode(), mcp_service_key.encode()):
        return ApiTokenUsage.MCP

    return None
