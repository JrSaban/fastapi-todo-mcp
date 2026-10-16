from collections.abc import Sequence

from src.core.exceptions import NotFoundError
from src.modules.audit.models import AuditAction, AuditResourceType
from src.modules.audit.service import AuditLogService
from src.modules.categories.models import Category
from src.modules.categories.repository import CategoryRepository
from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import TodoCreate, TodoUpdate
from src.modules.users.models import User

r"""
Service Layer (Logique métier pour les Todos).
Équivalent d'une classe TodoService ou d'Actions Laravel (App\Actions\CreateTodoAction).
"""


class TodoService:
    def __init__(
        self,
        repository: TodoRepository,
        category_repository: CategoryRepository,
        audit_service: AuditLogService,
    ) -> None:
        self.repository = repository
        self.category_repository = category_repository
        self.audit = audit_service

    def _compute_changes(
        self,
        todo: Todo,
        payload: TodoUpdate,
        categories: Sequence[Category] | None,
    ) -> list[str]:
        """Compute the changes between a todo and a payload."""
        changes = []
        for field, new in payload.model_dump(exclude_unset=True).items():
            if field == "category_ids":
                old = sorted(c.id for c in todo.categories)
                new = sorted(c.id for c in (categories or []))
            else:
                old = getattr(todo, field)

            if old != new:
                changes.append(field)

        return changes

    async def list_todos(
        self,
        current_user: User,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        title: str | None = None,
        is_completed: bool | None = None,
        category_ids: list[int] | None = None,
    ) -> tuple[Sequence[Todo], int]:
        """Fetch todos with pagination."""
        owner_id = current_user.id if not current_user.is_admin else owner_id

        todos = await self.repository.get_all(
            skip=skip,
            limit=limit,
            owner_id=owner_id,
            title=title,
            is_completed=is_completed,
            category_ids=category_ids,
        )
        total = await self.repository.count(
            owner_id=owner_id, title=title, is_completed=is_completed, category_ids=category_ids
        )

        return (todos, total)

    async def get_todo_or_404(
        self, current_user: User, entity_id: int, with_categories: bool = False
    ) -> Todo:
        """Get a todo or raise a 404 error."""
        owner_id = current_user.id if not current_user.is_admin else None
        todo = await self.repository.get_by_id(
            owner_id=owner_id, entity_id=entity_id, with_categories=with_categories
        )

        if not todo:
            raise NotFoundError(f"Tâche avec l'ID {entity_id} introuvable.")
        return todo

    async def create_todo(self, owner_id: int, data: TodoCreate) -> Todo:
        """Crée une nouvelle tâche."""
        categories = await self.category_repository.get_by_ids(data.category_ids)
        todo = await self.repository.create(owner_id=owner_id, data=data, categories=categories)

        await self.audit.record(
            resource_type=AuditResourceType.TODO,
            resource_id=todo.id,
            resource_owner_id=todo.owner_id,
            action=AuditAction.CREATED,
        )

        return todo

    async def update_todo(self, current_user: User, entity_id: int, data: TodoUpdate) -> Todo:
        """Met à jour une tâche existante."""
        todo = await self.get_todo_or_404(
            current_user=current_user,
            entity_id=entity_id,
            with_categories=data.category_ids is not None,
        )

        categories = None
        if data.category_ids is not None:
            categories = await self.category_repository.get_by_ids(data.category_ids)

        changed_fields = self._compute_changes(todo, data, categories)
        todo = await self.repository.update(todo, data, categories)

        if changed_fields:
            await self.audit.record(
                resource_type=AuditResourceType.TODO,
                resource_id=todo.id,
                resource_owner_id=todo.owner_id,
                action=AuditAction.UPDATED,
                changed_fields=changed_fields,
            )

        return todo

    async def delete_todo(self, current_user: User, entity_id: int) -> None:
        """Delete a todo."""
        todo = await self.get_todo_or_404(current_user=current_user, entity_id=entity_id)
        await self.repository.delete(todo)

        await self.audit.record(
            resource_type=AuditResourceType.TODO,
            resource_id=todo.id,
            resource_owner_id=todo.owner_id,
            action=AuditAction.DELETED,
        )

    async def delete_completed_todos(self, current_user: User) -> int:
        """Delete all completed todos."""
        todo_ids = await self.repository.get_completed_ids(owner_id=current_user.id)
        deleted_todo_ids = await self.repository.delete_completed(
            todo_ids=todo_ids, owner_id=current_user.id
        )

        for todo_id in deleted_todo_ids:
            await self.audit.record(
                resource_type=AuditResourceType.TODO,
                resource_id=todo_id,
                resource_owner_id=current_user.id,
                action=AuditAction.DELETED,
            )

        return len(deleted_todo_ids)

    # --- Methods for deleted todos ---

    async def list_deleted_todos(
        self,
        current_user: User,
        skip: int = 0,
        limit: int = 25,
        owner_id: int | None = None,
        title: str | None = None,
    ) -> tuple[Sequence[Todo], int]:
        """Fetch deleted todos with pagination."""
        owner_id = current_user.id if not current_user.is_admin else owner_id

        todos = await self.repository.get_all_deleted(
            skip=skip,
            limit=limit,
            owner_id=owner_id,
            title=title,
        )
        total = await self.repository.count(owner_id=owner_id, title=title, is_deleted=True)

        return (todos, total)

    async def get_deleted_todo_or_404(self, current_user: User, entity_id: int) -> Todo:
        """Get a deleted todo or raise a 404 error."""
        owner_id = current_user.id if not current_user.is_admin else None
        todo = await self.repository.get_deleted_by_id(owner_id=owner_id, entity_id=entity_id)

        if not todo:
            raise NotFoundError(f"Tâche avec l'ID {entity_id} introuvable.")
        return todo

    async def restore_todo(self, current_user: User, entity_id: int) -> Todo:
        """Restore a deleted todo."""
        todo = await self.get_deleted_todo_or_404(current_user=current_user, entity_id=entity_id)
        todo = await self.repository.restore(todo)

        await self.audit.record(
            resource_type=AuditResourceType.TODO,
            resource_id=todo.id,
            resource_owner_id=todo.owner_id,
            action=AuditAction.RESTORED,
        )

        return todo
