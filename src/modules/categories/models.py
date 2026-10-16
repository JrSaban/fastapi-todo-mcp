from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.modules.audit.models import AuditResourceType
from src.modules.todos_categories.models import todos_categories

if TYPE_CHECKING:
    from src.modules.todos.models import Todo


class Category(Base):
    """
    Modele SQLAlchemy pour la table 'categories'
    """

    __tablename__ = "categories"

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#FAFAFA")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    todos: Mapped[list["Todo"]] = relationship(
        secondary=todos_categories,
        back_populates="categories",
    )

    __table_args__ = (
        Index(
            "uq_partial_categories_name_lower",
            func.lower(name),
            unique=True,
            postgresql_where=deleted_at.is_(None),
            sqlite_where=deleted_at.is_(None),
        ),
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name} color={self.color}>"

    # Auditable properties
    audit_resource_type: ClassVar[AuditResourceType] = AuditResourceType.CATEGORY

    @property
    def audit_owner_id(self) -> int:
        return self.created_by_id
