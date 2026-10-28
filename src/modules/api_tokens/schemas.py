"""
DTOs and Validation with Pydantic V2 for the ApiToken domain.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.modules.api_tokens.models import ApiTokenUsage


class ApiTokenCreate(BaseModel):
    """Payload when creating a new ApiToken."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=2, max_length=100, description="Nom du token")
    usage: ApiTokenUsage = Field(default=ApiTokenUsage.API, description="Usage du token")
    current_password: str = Field(..., max_length=255, description="Mot de passe")
    expires_in_days: Literal[30, 90] | None = Field(
        default=90, description="Durée de vie du token (30 / 90 jours ou null pour illimité)"
    )


class ApiTokenResponse(BaseModel):
    """Serialized payload for ApiToken"""

    id: int
    name: str
    usage: ApiTokenUsage
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiTokenCreatedResponse(ApiTokenResponse):
    """Creation response: the only one that carries the plain token, shown once."""

    token: str
