"""DTOs and Validation with Pydantic V2 for the ApprovalRequest domain."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, JsonValue

from src.modules.approvals.models import ApprovalAction, ApprovalStatus


class ApprovalRequestResponse(BaseModel):
    """Serialized payload for approval request"""

    id: int
    owner_id: int
    action: ApprovalAction
    status: ApprovalStatus
    payload: dict[str, JsonValue]
    result: dict[str, JsonValue] | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
