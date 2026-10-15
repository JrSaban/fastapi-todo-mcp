"""Routing & Controller Layer pour le domaine Approvals."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.core.database import DbSessionDep
from src.core.schemas import LimitQuery, PaginatedResponse
from src.modules.approvals.models import ApprovalStatus
from src.modules.approvals.repository import ApprovalRequestRepository
from src.modules.approvals.schemas import ApprovalRequestResponse
from src.modules.approvals.service import ApprovalRequestService
from src.modules.auth.router import CurrentUserDep, SessionUserDep
from src.modules.todos.repository import TodoRepository
from src.modules.users.repository import UserRepository

router = APIRouter(prefix="/approval-requests", tags=["ApprovalRequests"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_approval_request_service(
    session: DbSessionDep,
) -> ApprovalRequestService:
    repository = ApprovalRequestRepository(session)
    todo_repository = TodoRepository(session)
    user_repository = UserRepository(session)
    return ApprovalRequestService(repository, todo_repository, user_repository)


# Type alias pour injection propre et lisible (standard Python moderne)
ApprovalRequestServiceDep = Annotated[ApprovalRequestService, Depends(get_approval_request_service)]


@router.get(
    "",
    response_model=PaginatedResponse[ApprovalRequestResponse],
    summary="Lister toutes les demandes d'approbation",
    description="Retourne une liste paginée de demandes d'approbation.",
)
async def list_approval_requests(
    service: ApprovalRequestServiceDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    owner_id: Annotated[
        int | None, Query(description="Filtre sur le propriétaire (Seulement pour les admins)")
    ] = None,
    status: Annotated[
        ApprovalStatus | None, Query(description="Filtre sur le statut de la demande d'approbation")
    ] = None,
) -> PaginatedResponse[ApprovalRequestResponse]:
    approval_requests, total = await service.list_approval_requests(
        current_user=current_user,
        skip=skip,
        limit=limit,
        owner_id=owner_id,
        status=status,
    )

    return PaginatedResponse(items=approval_requests, total=total, skip=skip, limit=limit)


@router.get(
    "/{request_id}",
    response_model=ApprovalRequestResponse,
    summary="Afficher une demande d'approbation",
    description="Récupère une demande d'approbation.",
)
async def get_approval_request(
    service: ApprovalRequestServiceDep,
    current_user: CurrentUserDep,
    request_id: int,
) -> ApprovalRequestResponse:
    approval_request = await service.get_approval_request_or_404(
        current_user=current_user, entity_id=request_id
    )

    return ApprovalRequestResponse.model_validate(approval_request)


@router.post(
    "/{request_id}/approve",
    response_model=ApprovalRequestResponse,
    summary="Approuver une demande d'approbation",
    description="Approuve une demande d'approbation.",
)
async def approve_request(
    service: ApprovalRequestServiceDep,
    current_user: SessionUserDep,
    request_id: int,
) -> ApprovalRequestResponse:
    approval_request = await service.approve(current_user=current_user, entity_id=request_id)
    return ApprovalRequestResponse.model_validate(approval_request)


@router.post(
    "/{request_id}/reject",
    response_model=ApprovalRequestResponse,
    summary="Rejeter une demande d'approbation",
    description="Rejette une demande d'approbation.",
)
async def reject_request(
    service: ApprovalRequestServiceDep,
    current_user: SessionUserDep,
    request_id: int,
) -> ApprovalRequestResponse:
    approval_request = await service.reject(current_user=current_user, entity_id=request_id)
    return ApprovalRequestResponse.model_validate(approval_request)
