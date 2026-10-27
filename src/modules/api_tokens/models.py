"""Modèles pour le domaine API Tokens."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base
from src.modules.audit.models import AuditResourceType


class ApiTokenUsage(StrEnum):
    """What an API token is for, chosen at creation and never changed.

    An `api` token is used directly against the API (scripts, the local MCP server). An `mcp`
    token is only accepted through the remote MCP server, which proves it with its service key.
    """

    API = "api"
    MCP = "mcp"


class ApiToken(Base):
    """Modèle SQLAlchemy pour la table 'api_tokens'."""

    __tablename__ = "api_tokens"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    usage: Mapped[ApiTokenUsage] = mapped_column(
        Enum(
            ApiTokenUsage,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=10,
            create_constraint=False,
        ),
        default=ApiTokenUsage.API,
        server_default=ApiTokenUsage.API.value,
        nullable=False,
    )

    # Timestamps automatiques (created_at) gérés côté base de données
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"usage IN ({', '.join(f"'{usage.value}'" for usage in ApiTokenUsage)})",
            name="apitokenusage",
        ),
    )

    def __repr__(self) -> str:
        return f"<ApiToken owner_id={self.owner_id} name={self.name!r}>"

    # Auditable properties
    audit_resource_type: ClassVar[AuditResourceType] = AuditResourceType.API_TOKEN

    @property
    def audit_owner_id(self) -> int:
        return self.owner_id
