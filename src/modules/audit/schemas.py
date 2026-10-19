"""DTOs and Validation with Pydantic V2 for the AuditLog domain."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditResourceType


class AuditLogListResponse(BaseModel):
    """Serialized payload for audit logs list"""

    id: int
    actor_id: int | None
    api_token_id: int | None
    resource_type: AuditResourceType
    resource_id: int
    resource_owner_id: int | None
    action: AuditAction
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(AuditLogListResponse):
    """Serialized payload for audit logs detailed view"""

    auth_method: AuditAuthMethod | None
    changed_fields: list[str] | None
