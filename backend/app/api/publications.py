"""Owner-only visibility and explicit resolution of uncertain publication outcomes."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.connections import bound_database, context
from app.api.security import require_session
from app.services.publication_recovery import (
    RecoveryError, quiesce_attempt, recovery_status, resolve_outcome,
)

router = APIRouter(prefix="/api/v1/publications")


class ResolutionInput(BaseModel):
    attempt_id: str = Field(min_length=10, max_length=200)
    expected_workflow_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)
    outcome: Literal["published", "not_published"]
    acknowledgement: bool
    post_url: str | None = Field(default=None, max_length=500)


@router.get("/{workflow_id}/recovery")
async def read_recovery(workflow_id: str, request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        workflow = await db.workflows.find_one({"_id": workflow_id,
            "installation_id": identity})
        if not workflow:
            raise HTTPException(404, "Workflow not found")
        try:
            return await recovery_status(db, workflow_id)
        except RecoveryError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/{workflow_id}/quiesce")
async def quiesce(workflow_id: str, request: Request):
    require_session(request, write=True)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        try:
            workflow = await db.workflows.find_one({"_id": workflow_id,
                "installation_id": identity})
            if not workflow:
                raise HTTPException(404, "Workflow not found")
            status = await recovery_status(db, workflow_id)
            return await quiesce_attempt(db, settings.app_data_dir, status["attempt_id"])
        except RecoveryError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post("/{workflow_id}/resolve")
async def resolve(workflow_id: str, body: ResolutionInput, request: Request):
    require_session(request, write=True)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        try:
            return await resolve_outcome(db, settings.app_data_dir, identity,
                workflow_id=workflow_id, attempt_id=body.attempt_id,
                expected_revision=body.expected_workflow_revision,
                idempotency_key=body.idempotency_key, outcome=body.outcome,
                acknowledgement=body.acknowledgement,
                post_url=body.post_url.strip() if body.post_url else None)
        except RecoveryError as exc:
            raise HTTPException(409, str(exc)) from exc
