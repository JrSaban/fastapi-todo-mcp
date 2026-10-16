from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.modules.audit.models import AuditResourceType
from src.modules.todos_categories.models import todos_categories

if TYPE_CHECKING:
    from src.modules.categories.models import Category


class Todo(Base):
    """Modèle SQLAlchemy pour la table 'todos'."""

    __tablename__ = "todos"

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps automatiques (created_at / updated_at) gérés côté base de données
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    categories: Mapped[list["Category"]] = relationship(
        secondary=todos_categories,
        back_populates="todos",
    )

    def __repr__(self) -> str:
        return f"<Todo id={self.id} title={self.title!r} is_completed={self.is_completed}>"

    # Auditable properties
    audit_resource_type: ClassVar[AuditResourceType] = AuditResourceType.TODO

    @property
    def audit_owner_id(self) -> int:
        return self.owner_id
