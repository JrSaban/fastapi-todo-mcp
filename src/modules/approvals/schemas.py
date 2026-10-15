"""DTOs and Validation with Pydantic V2 for the ApprovalRequest domain."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from src.modules.approvals.models import ApprovalAction


class ApprovalRequestState(StrEnum):
    """Approval request state."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequestResponse(BaseModel):
    """Serialized payload for approval request"""

    id: int
    owner_id: int
    action: ApprovalAction
    status: ApprovalRequestState
    payload: dict[str, JsonValue]
    result: dict[str, JsonValue] | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def compute_status(self) -> "ApprovalRequestResponse":
        if self.status != ApprovalRequestState.PENDING:
            return self

        if self.expires_at.tzinfo is None:
            self.expires_at = self.expires_at.replace(tzinfo=UTC)

        if self.expires_at <= datetime.now(UTC):
            self.status = ApprovalRequestState.EXPIRED
        return self
