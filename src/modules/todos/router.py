"""
Routing & Controller Layer pour le domaine Todos.
Équivalent de routes/api.php et TodoController.php dans Laravel.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from src.core.database import DbSessionDep
from src.core.schemas import LimitQuery, PaginatedResponse
from src.modules.auth.router import CurrentUserDep
from src.modules.categories.repository import CategoryRepository
from src.modules.todos.models import Todo
from src.modules.todos.repository import TodoRepository
from src.modules.todos.schemas import (
    DeletedTodoResponse,
    TodoCreate,
    TodoDetailResponse,
    TodoResponse,
    TodoUpdate,
)
from src.modules.todos.service import TodoService

router = APIRouter(prefix="/todos", tags=["Todos"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_todo_service(
    session: DbSessionDep,
) -> TodoService:
    repository = TodoRepository(session)
    category_repository = CategoryRepository(session)
    return TodoService(repository, category_repository)


# Type alias pour injection propre et lisible (standard Python moderne)
TodoServiceDep = Annotated[TodoService, Depends(get_todo_service)]


@router.get(
    "",
    response_model=PaginatedResponse[TodoResponse],
    summary="Lister toutes les tâches",
    description="Retourne une liste paginée de tâches.",
)
async def list_todos(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    owner_id: Annotated[
        int | None, Query(description="Filtre sur le propriétaire (Seulement pour les admins)")
    ] = None,
    title: Annotated[str | None, Query(min_length=2, description="Filtre sur le titre")] = None,
    is_completed: Annotated[
        bool | None, Query(description="Filtre sur le statut de complétion")
    ] = None,
    category_ids: Annotated[
        list[int] | None, Query(max_length=20, description="Filtre sur les catégories")
    ] = None,
) -> PaginatedResponse[TodoResponse]:
    todos, total = await service.list_todos(
        current_user=current_user,
        skip=skip,
        limit=limit,
        owner_id=owner_id,
        title=title,
        is_completed=is_completed,
        category_ids=category_ids,
    )

    return PaginatedResponse(items=todos, total=total, skip=skip, limit=limit)


@router.post(
    "",
    response_model=TodoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une tâche",
    description="Crée une nouvelle tâche et la persiste en base de données.",
)
async def create_todo(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    data: TodoCreate,
) -> Todo:
    return await service.create_todo(owner_id=current_user.id, data=data)


@router.get(
    "/deleted",
    response_model=PaginatedResponse[DeletedTodoResponse],
    summary="Lister toutes les tâches supprimées",
    description="Retourne une liste paginée de tâches supprimées.",
)
async def list_deleted_todos(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    owner_id: Annotated[
        int | None, Query(description="Filtre sur le propriétaire (Seulement pour les admins)")
    ] = None,
    title: Annotated[str | None, Query(min_length=2, description="Filtre sur le titre")] = None,
) -> PaginatedResponse[DeletedTodoResponse]:
    deleted_todos, total = await service.list_deleted_todos(
        current_user=current_user,
        skip=skip,
        limit=limit,
        owner_id=owner_id,
        title=title,
    )

    return PaginatedResponse(items=deleted_todos, total=total, skip=skip, limit=limit)


@router.get(
    "/{todo_id}",
    response_model=TodoResponse | TodoDetailResponse,
    summary="Afficher une tâche",
    description="Récupère une tâche. `?include=categories` pour inclure ses catégories.",
)
async def get_todo(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    todo_id: int,
    include: Annotated[list[str] | None, Query()] = None,
) -> TodoResponse | TodoDetailResponse:
    with_categories = include is not None and "categories" in include
    todo = await service.get_todo_or_404(
        current_user=current_user, entity_id=todo_id, with_categories=with_categories
    )

    if with_categories:
        return TodoDetailResponse.model_validate(todo)
    return TodoResponse.model_validate(todo)


@router.patch(
    "/{todo_id}",
    response_model=TodoResponse,
    summary="Mettre à jour une tâche",
    description="Met à jour partiellement les champs d'une tâche existante.",
)
async def update_todo(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    todo_id: int,
    data: TodoUpdate,
) -> Todo:
    return await service.update_todo(current_user=current_user, entity_id=todo_id, data=data)


@router.delete(
    "/{todo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une tâche",
    description="Supprime une tâche de la base de données.",
)
async def delete_todo(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    todo_id: int,
) -> None:
    await service.delete_todo(current_user=current_user, entity_id=todo_id)


@router.post(
    "/{todo_id}/restore",
    response_model=TodoResponse,
    summary="Restaurer une tâche",
    description="Restaure une tâche supprimée.",
)
async def restore_todo(
    service: TodoServiceDep,
    current_user: CurrentUserDep,
    todo_id: int,
) -> Todo:
    return await service.restore_todo(current_user=current_user, entity_id=todo_id)
