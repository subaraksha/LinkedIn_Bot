from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class WorkflowState(StrEnum):
    RESEARCH_PENDING = "RESEARCH_PENDING"
    AWAITING_TOPIC = "AWAITING_TOPIC"
    AWAITING_INPUT = "AWAITING_INPUT"
    GENERATING = "GENERATING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PUBLISH_PENDING = "PUBLISH_PENDING"
    PUBLISHING = "PUBLISHING"
    PUBLISH_UNKNOWN = "PUBLISH_UNKNOWN"
    PAUSED = "PAUSED"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    DISCARDED = "DISCARDED"


class OwnerIdentity(BaseModel):
    installation_id: str
    revision: int = Field(ge=0)


class ConnectionStatus(BaseModel):
    provider: str
    state: str
    checked_at: datetime | None = None
    action_required: str | None = None


class JobReference(BaseModel):
    id: str
    kind: str
    dedupe_key: str
    expected_revision: int = Field(ge=0)


class ApiError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ApiError
    request_id: str
