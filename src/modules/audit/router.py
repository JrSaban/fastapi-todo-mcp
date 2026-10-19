"""Routing & Controller Layer pour le module Audit."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.core.database import DbSessionDep
from src.core.schemas import LimitQuery, PaginatedResponse
from src.modules.audit.models import AuditAction, AuditResourceType
from src.modules.audit.repository import AuditLogRepository
from src.modules.audit.schemas import AuditLogResponse
from src.modules.audit.service import AuditLogService
from src.modules.auth.router import SessionUserDep

router = APIRouter(prefix="/audit-logs", tags=["AuditLogs"])


# Factory de dépendance : instancie Repository et Service injectés par requête
def get_audit_log_service(
    session: DbSessionDep,
) -> AuditLogService:
    repository = AuditLogRepository(session)
    return AuditLogService(repository)


# Type alias pour injection propre et lisible (standard Python moderne)
AuditLogServiceDep = Annotated[AuditLogService, Depends(get_audit_log_service)]


@router.get(
    "",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Lister tous les logs d'audit",
    description="Retourne une liste paginée de logs d'audit.",
)
async def list_audit_logs(
    service: AuditLogServiceDep,
    current_user: SessionUserDep,
    skip: Annotated[int, Query(ge=0, description="Nombre d'éléments à sauter")] = 0,
    limit: LimitQuery = 25,
    owner_id: Annotated[
        int | None,
        Query(description="Filtre sur le propriétaire de l'entité (Seulement pour les admins)"),
    ] = None,
    action: Annotated[AuditAction | None, Query(description="Filtre sur l'action")] = None,
    resource_type: Annotated[
        AuditResourceType | None, Query(description="Filtre sur le type de ressource")
    ] = None,
    resource_id: Annotated[int | None, Query(description="Filtre sur l'ID de la ressource")] = None,
    api_token_id: Annotated[int | None, Query(description="Filtre sur l'ID du token API")] = None,
) -> PaginatedResponse[AuditLogResponse]:
    audit_logs, total = await service.list_audit_logs(
        current_user=current_user,
        skip=skip,
        limit=limit,
        resource_owner_id=owner_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        api_token_id=api_token_id,
    )

    return PaginatedResponse(items=audit_logs, total=total, skip=skip, limit=limit)


@router.get(
    "/{audit_log_id}",
    response_model=AuditLogResponse,
    summary="Afficher un log d'audit",
    description="Récupère un log d'audit.",
)
async def get_audit_log(
    service: AuditLogServiceDep,
    current_user: SessionUserDep,
    audit_log_id: int,
) -> AuditLogResponse:
    audit_log = await service.get_audit_log_or_404(
        current_user=current_user, entity_id=audit_log_id
    )

    return AuditLogResponse.model_validate(audit_log)
