"""Owner-only Phase 3 schedule and conversation controls."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.api.connections import bound_database, context
from app.api.security import require_session
from app.services.topic_workflow import (
    TopicWorkflowError, current_workflow, get_schedule, save_schedule, start_workflow,
)
from app.services.publication_recovery import RecoveryError, recovery_status


router = APIRouter(prefix="/api/v1/topics")


class ScheduleInput(BaseModel):
    expected_revision: int = Field(ge=0)
    enabled: bool
    weekday: int = Field(ge=0, le=6)
    local_time: str = Field(min_length=5, max_length=5)
    timezone: str = Field(min_length=3, max_length=80)


@router.get("/status")
async def status(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        workflow = await current_workflow(db, identity)
        issue_count = (await db.messages.count_documents({"kind": "phase3_topic",
            "workflow_id": workflow["_id"], "status": "send_unknown"}) if workflow else 0)
        drafts = (await db.draft_versions.find({"workflow_id": workflow["_id"]},
            {"_id": 1, "version": 1, "body": 1, "restored_from": 1,
             "used_fact_ids": 1, "source_ids": 1, "created_at": 1}).sort(
                "version", -1).limit(30).to_list(length=30) if workflow else [])
        history = await db.workflows.find({"installation_id": identity, "active": False,
            "state": {"$in": ["PUBLISHED", "DISCARDED", "SKIPPED"]}},
            {"_id": 1, "state": 1, "selected_topic.title": 1, "post_url": 1,
             "post_evidence_source": 1, "updated_at": 1}).sort(
                "updated_at", -1).limit(20).to_list(length=20)
        recovery = None
        if workflow and workflow["state"] == "PUBLISH_UNKNOWN":
            try:
                recovery = await recovery_status(db, workflow["_id"])
            except RecoveryError:
                recovery = {"error": "Recovery evidence is unavailable"}
        return {"schedule": await get_schedule(db, identity, settings.app_timezone),
                "workflow": workflow,
                "drafts": drafts,
                "recent_outcomes": history,
                "recovery": recovery,
                "publishing_enabled": settings.publishing_enabled,
                "telegram_delivery_uncertain": issue_count > 0}


@router.put("/schedule")
async def update_schedule(request: Request, body: ScheduleInput):
    require_session(request, write=True)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        if body.enabled:
            connection = await db.connections.find_one({"_id": "telegram", "installation_id": identity,
                                                       "status": "connected"})
            if not connection:
                raise HTTPException(409, "Pair Telegram before enabling invitations")
        try:
            return await save_schedule(db, identity, expected_revision=body.expected_revision,
                weekday=body.weekday, local_time=body.local_time,
                timezone_name=body.timezone, enabled=body.enabled)
        except TopicWorkflowError as exc:
            raise HTTPException(409, str(exc)) from exc
        except DuplicateKeyError:
            raise HTTPException(409, "Schedule changed; refresh before saving") from None


@router.post("/start", status_code=201)
async def start_now(request: Request):
    require_session(request, write=True)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        connection = await db.connections.find_one({"_id": "telegram", "installation_id": identity,
                                                   "status": "connected"})
        if not connection:
            raise HTTPException(409, "Pair Telegram before starting topic research")
        try:
            row = await start_workflow(db, identity,
                slot_key="manual:" + datetime.now(timezone.utc).isoformat(), origin="manual")
        except TopicWorkflowError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": row["_id"], "state": row["state"]}
