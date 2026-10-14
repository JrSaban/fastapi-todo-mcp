"""
Repository Pattern pour l'accès aux données de Todo.
Encapsule les requêtes SQL (SQLAlchemy 2.0 select, add, delete).
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.repository import FilterParams, SoftDeleteRepository
from src.modules.categories.models import Category
from src.modules.todos.models import Todo
from src.modules.todos.schemas import TodoCreate, TodoUpdate


class TodoRepository(SoftDeleteRepository[Todo]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Todo)

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        title: str | None = None,
        is_completed: bool | None = None,
        category_ids: list[int] | None = None,
    ) -> Sequence[Todo]:
        """Récupère une liste paginée de tâches."""
        query = select(Todo).order_by(Todo.id.desc())
        query = self._apply_filters(
            query,
            owner_id=owner_id,
            title=title,
            is_completed=is_completed,
            category_ids=category_ids,
        )

        return await self.paginate(query, skip, limit)

    async def get_by_id(  # pyrefly: ignore[bad-override]
        self, entity_id: int, *, owner_id: int | None, with_categories: bool = False
    ) -> Todo | None:
        """Récupère une tâche par son identifiant unique."""
        query = select(Todo).where(Todo.id == entity_id, Todo.deleted_at.is_(None))

        if owner_id is not None:
            query = query.where(Todo.owner_id == owner_id)

        if with_categories:
            query = query.options(selectinload(Todo.categories.and_(Category.deleted_at.is_(None))))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create(self, owner_id: int, data: TodoCreate, categories: Sequence[Category]) -> Todo:
        """Crée et persiste une nouvelle tâche."""
        todo = Todo(
            owner_id=owner_id,
            title=data.title,
            description=data.description,
            categories=list(categories),
        )
        self.session.add(todo)
        # Génère l'ID via PostgreSQL sans commiter la transaction globale
        await self.session.flush()
        await self.session.refresh(todo)
        return todo

    async def update(  # pyrefly: ignore[bad-override]
        self, todo: Todo, data: TodoUpdate, categories: Sequence[Category] | None = None
    ) -> Todo:
        """Met à jour une tâche existante avec les champs fournis."""
        update_data = data.model_dump(exclude_unset=True, exclude={"category_ids"})
        for field, value in update_data.items():
            setattr(todo, field, value)

        if categories is not None:
            todo.categories = list(categories)
            todo.updated_at = func.now()

        self.session.add(todo)
        await self.session.flush()
        await self.session.refresh(todo)
        return todo

    async def delete(self, entity: Todo) -> None:
        """Soft delete the entity by setting the deleted_at field to the current time."""
        entity.deleted_at = datetime.now(UTC)
        self.session.add(entity)
        await self.session.flush()

    # --- CRUD for deleted entities ---

    async def get_all_deleted(
        self,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        title: str | None = None,
    ) -> Sequence[Todo]:
        """Fetch deleted todos only."""
        query = select(Todo).order_by(Todo.deleted_at.desc())
        query = self._apply_filters(
            query,
            owner_id=owner_id,
            title=title,
            is_deleted=True,
        )

        return await self.paginate(query, skip, limit)

    async def get_deleted_by_id(self, entity_id: int, *, owner_id: int | None) -> Todo | None:
        """Get a deleted todo by its ID."""
        query = select(Todo).where(Todo.id == entity_id, Todo.deleted_at.is_not(None))

        if owner_id is not None:
            query = query.where(Todo.owner_id == owner_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # --- utility methods ---

    async def count(
        self,
        owner_id: int | None = None,
        title: str | None = None,
        is_completed: bool | None = None,
        category_ids: list[int] | None = None,
        *,
        is_deleted: bool = False,
    ) -> int:
        query = select(func.count()).select_from(Todo)
        query = self._apply_filters(
            query,
            owner_id=owner_id,
            title=title,
            is_completed=is_completed,
            category_ids=category_ids,
            is_deleted=is_deleted,
        )

        return await self.count_query(query)

    def _apply_filters(
        self,
        query: Select,
        owner_id: int | None = None,
        title: str | None = None,
        is_completed: bool | None = None,
        category_ids: list[int] | None = None,
        *,
        is_deleted: bool = False,
    ) -> Select:
        """Apply filters on a query."""
        query = self._apply_filter_params(
            query,
            [
                FilterParams(column=Todo.owner_id, value=owner_id, op="eq"),
                FilterParams(column=Todo.title, value=title, op="ilike"),
                FilterParams(column=Todo.is_completed, value=is_completed, op="eq"),
            ],
        )

        if category_ids is not None:
            query = query.where(
                Todo.categories.any(
                    and_(
                        Category.id.in_(category_ids),
                        Category.deleted_at.is_(None),
                    )
                )
            )

        if is_deleted:
            query = query.where(Todo.deleted_at.is_not(None))
        else:
            query = query.where(Todo.deleted_at.is_(None))

        return query
