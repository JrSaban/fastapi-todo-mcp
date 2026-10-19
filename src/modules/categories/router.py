"""
Routing & Controller Layer pour le domaine Categories.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis

from src.core.database import DbSessionDep
from src.core.redis import get_redis_client
from src.core.schemas import LimitQuery, PaginatedResponse
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.service import AuditLogService
from src.modules.auth.router import CurrentUserDep, SessionUserDep
from src.modules.categories.models import Category
from src.modules.categories.repository import CategoryRepository
from src.modules.categories.schemas import CategoryCreate, CategoryResponse, CategoryUpdate
from src.modules.categories.service import CategoryService
from src.modules.todos.router import TodoServiceDep
from src.modules.todos.schemas import TodoResponse

router = APIRouter(prefix="/categories", tags=["Categories"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_category_service(
    session: DbSessionDep,
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> CategoryService:
    repository = CategoryRepository(session)
    audit_service = AuditLogService(repository=AuditLogRepository(session))
    return CategoryService(repository, redis_client, audit_service)


# Type alias pour injection propre et lisible (standard Python moderne)
CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]


@router.get(
    "",
    response_model=PaginatedResponse[CategoryResponse],
    summary="Lister toutes les catégories",
    description="Retourne une liste paginée de catégories.",
)
async def list_categories(
    service: CategoryServiceDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0, le=2000, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    name: Annotated[str | None, Query(min_length=2, description="Filtre sur le nom")] = None,
) -> PaginatedResponse[CategoryResponse]:
    categories, total = await service.list_categories(skip=skip, limit=limit, name=name)
    return PaginatedResponse(items=categories, total=total, skip=skip, limit=limit)


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une catégorie.",
    description="Crée une nouvelle catégorie.",
)
async def create_category(
    service: CategoryServiceDep,
    current_user: SessionUserDep,
    data: CategoryCreate,
) -> Category:
    return await service.create_category(created_by_id=current_user.id, data=data)


@router.get(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="Afficher une catégorie",
    description="Récupère les détails d'une catégorie par son ID. ",
)
async def get_category(
    service: CategoryServiceDep,
    current_user: CurrentUserDep,
    category_id: int,
) -> CategoryResponse:
    return await service.get_category_cached(category_id)


@router.get(
    "/{category_id}/todos",
    response_model=PaginatedResponse[TodoResponse],
    summary="Lister les tâches d'une catégorie",
    description="Récupère toutes les tâches associées à une catégorie.",
)
async def list_todos_by_category(
    service: CategoryServiceDep,
    todo_service: TodoServiceDep,
    current_user: CurrentUserDep,
    category_id: int,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
) -> PaginatedResponse[TodoResponse]:
    """Récupère toutes les tâches associées à une catégorie."""
    await service.get_category_or_404(category_id)
    todos, total = await todo_service.list_todos(
        current_user=current_user, skip=skip, limit=limit, category_ids=[category_id]
    )
    return PaginatedResponse(items=todos, total=total, skip=skip, limit=limit)


@router.patch(
    "/{category_id}",
    response_model=CategoryResponse,
    summary="Mettre à jour une catégorie",
    description="Met à jour partiellement les champs d'une catégorie existante.",
)
async def update_category(
    service: CategoryServiceDep,
    current_user: SessionUserDep,
    category_id: int,
    data: CategoryUpdate,
) -> Category:
    return await service.update_category(user=current_user, entity_id=category_id, data=data)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une catégorie.",
    description="Supprime une catégorie de la base de données.",
)
async def delete_category(
    service: CategoryServiceDep,
    current_user: CurrentUserDep,
    category_id: int,
) -> None:
    await service.delete_category(user=current_user, entity_id=category_id)
