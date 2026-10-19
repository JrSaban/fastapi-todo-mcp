"""DTOs and Validation with Pydantic V2 for the AuditLog domain."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.modules.audit.models import AuditAction, AuditAuthMethod, AuditResourceType


class AuditLogResponse(BaseModel):
    """Serialized payload for audit logs"""

    id: int
    actor_id: int | None
    auth_method: AuditAuthMethod | None
    api_token_id: int | None
    resource_type: AuditResourceType
    resource_id: int
    resource_owner_id: int
    action: AuditAction
    changed_fields: list[str] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
