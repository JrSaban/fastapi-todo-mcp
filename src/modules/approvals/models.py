"""Modèles pour le domaine Approvals."""

from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue
from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base


class ApprovalAction(StrEnum):
    """Approval action types."""

    DELETE_COMPLETED_TODOS = "delete_completed_todos"


class ApprovalStatus(StrEnum):
    """Approval's status."""

    PENDING = "pending"
    REJECTED = "rejected"
    APPROVED = "approved"


class Approval(Base):
    """Modèle SQLAlchemy pour la table 'approvals'."""

    __tablename__ = "approval_requests"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[ApprovalAction] = mapped_column(
        Enum(
            ApprovalAction,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=20,
            create_constraint=False,
        ),
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            length=20,
            create_constraint=False,
        ),
        default=ApprovalStatus.PENDING,
        nullable=False,
    )
    payload: Mapped[JsonValue] = mapped_column(JSON)
    result: Mapped[JsonValue | None] = mapped_column(JSON)

    # Timestamps automatiques gérés côté base de données
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(f"'{status.value}'" for status in ApprovalStatus)})",
            name="approvalstatus",
        ),
        CheckConstraint(
            f"action IN ({', '.join(f"'{status.value}'" for status in ApprovalAction)})",
            name="approvalaction",
        ),
    )

    def __repr__(self) -> str:
        return f"<Approval id={self.id} action={self.action!r}>"
