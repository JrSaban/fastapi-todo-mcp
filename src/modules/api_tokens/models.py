"""Modèles pour le domaine API Tokens."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class ApiToken(Base):
    """Modèle SQLAlchemy pour la table 'api_tokens'."""

    __tablename__ = "api_tokens"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)

    # Timestamps automatiques (created_at) gérés côté base de données
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<ApiToken owner_id={self.owner_id} name={self.name!r}>"
