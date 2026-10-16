"""
Modèles pour le domaine Audit : le journal de toutes les écritures (qui, comment, quoi, quand).

Une entrée ne référence ni la ressource ni le jeton d'API par une clé étrangère : l'historique
doit survivre à la purge des todos, des catégories et des jetons. Pour une modification, seuls les
noms des champs modifiés sont gardés, jamais leurs valeurs.
Équivalent conceptuel de spatie/laravel-activitylog dans Laravel.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class AuditAuthMethod(StrEnum):
    """Audit auth method types."""

    SESSION = "session"
    API_TOKEN = "api_token"


class AuditResourceType(StrEnum):
    """Audit resource types."""

    TODO = "todo"
    CATEGORY = "category"
    USER = "user"
    API_TOKEN = "api_token"
    APPROVAL_REQUEST = "approval_request"


class AuditAction(StrEnum):
    """Audit action types."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESTORED = "restored"


class AuditLog(Base):
    """Modèle SQLAlchemy pour la table 'audit_logs'."""

    __tablename__ = "audit_logs"

    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    auth_method: Mapped[AuditAuthMethod | None] = mapped_column(
        Enum(
            AuditAuthMethod,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=40,
            create_constraint=False,
        ),
        nullable=True,
    )
    api_token_id: Mapped[int | None] = mapped_column(nullable=True)
    resource_type: Mapped[AuditResourceType] = mapped_column(
        Enum(
            AuditResourceType,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=50,
            create_constraint=False,
        ),
        nullable=False,
    )
    resource_id: Mapped[int] = mapped_column(nullable=False)
    resource_owner_id: Mapped[int] = mapped_column(nullable=False, index=True)
    action: Mapped[AuditAction] = mapped_column(
        Enum(
            AuditAction,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=30,
            create_constraint=False,
        ),
        nullable=False,
    )
    changed_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Timestamps automatiques gérés côté base de données
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"auth_method IN ({', '.join(f"'{method.value}'" for method in AuditAuthMethod)})",
            name="auditauthmethod",
        ),
        CheckConstraint(
            f"resource_type IN ({', '.join(f"'{res.value}'" for res in AuditResourceType)})",
            name="auditresourcetype",
        ),
        CheckConstraint(
            f"action IN ({', '.join(f"'{action.value}'" for action in AuditAction)})",
            name="auditaction",
        ),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} resource_type={self.resource_type} action={self.action!r}>"
