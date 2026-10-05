"""Owner-only Phase 3 schedule and conversation controls."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.api.connections import bound_database, context
from app.api.security import require_session
from app.services.topic_workflow import (
    TopicWorkflowError, current_workflow, get_schedule, save_schedule,
    set_schedule_paused, start_workflow,
)
from app.services.publication_recovery import RecoveryError, recovery_status


router = APIRouter(prefix="/api/v1/topics")


class ScheduleInput(BaseModel):
    expected_revision: int = Field(ge=0)
    enabled: bool
    weekday: int = Field(ge=0, le=6)
    local_time: str = Field(min_length=5, max_length=5)
    timezone: str = Field(min_length=3, max_length=80)


class PauseInput(BaseModel):
    expected_revision: int = Field(ge=0)
    paused: bool


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
             "post_evidence_source": 1, "created_at": 1, "updated_at": 1,
             "draft_version": 1}).sort(
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


@router.put("/schedule/pause")
async def pause_schedule(request: Request, body: PauseInput):
    require_session(request, write=True)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        if not body.paused:
            connection = await db.connections.find_one({"_id": "telegram",
                "installation_id": identity, "status": "connected"})
            if not connection:
                raise HTTPException(409, "Pair Telegram before resuming invitations")
        try:
            return await set_schedule_paused(db, identity, settings.app_timezone,
                expected_revision=body.expected_revision, paused=body.paused)
        except TopicWorkflowError as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get("/history/{workflow_id}")
async def history_detail(request: Request, workflow_id: str):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        workflow = await db.workflows.find_one({"_id": workflow_id,
            "installation_id": identity, "kind": "weekly_topics", "active": False,
            "state": {"$in": ["PUBLISHED", "DISCARDED", "SKIPPED"]}},
            {"_id": 1, "state": 1, "selected_topic": 1, "perspective": 1,
             "post_url": 1, "post_evidence_source": 1,
             "created_at": 1, "updated_at": 1})
        if not workflow:
            raise HTTPException(404, "Conversation not found")
        drafts = await db.draft_versions.find({"workflow_id": workflow_id},
            {"_id": 1, "version": 1, "body": 1, "feedback": 1,
             "restored_from": 1, "created_at": 1}).sort("version", 1).limit(50).to_list(length=50)
        return {"workflow": workflow, "drafts": drafts}


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


@router.post("/discovery/refresh", status_code=202)
async def refresh_discovery(request: Request):
    require_session(request, write=True)
    from app.storage.jobs import enqueue_job
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        # Coalesce concurrent manual refreshes into a fifteen-minute window.
        slot = int(datetime.now(timezone.utc).timestamp()) // 900
        job = await enqueue_job(db, kind="discover_trends", dedupe_key=f"manual-discovery:{identity}:{slot}",
                                payload={"installation_id": identity})
        return {"job_id": job["_id"], "status": job["status"]}


@router.get("/discovery")
async def discovery_status(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        items = await db.trend_candidates.find({"collected_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=7)}}, {"signals": 0, "excerpt": 0}).sort(
            "trend_score", -1).limit(10).to_list(length=10)
        job = await db.jobs.find_one({"kind": "discover_trends", "payload.installation_id": identity},
                                    sort=[("created_at", -1)])
        return {"items": items, "timezone": settings.app_timezone, "job": {k: job.get(k) for k in ("_id", "status", "updated_at", "error_code")} if job else None}


class RegenerateInput(BaseModel):
    expected_revision: int = Field(ge=1)


@router.post('/regenerate', status_code=202)
async def regenerate(request: Request, body: RegenerateInput):
    require_session(request, write=True)
    from app.services.topic_workflow import regenerate_topics
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        try:
            return await regenerate_topics(db, identity, body.expected_revision)
        except (TopicWorkflowError, DuplicateKeyError) as exc:
            raise HTTPException(409, 'Conversation changed; refresh before regenerating suggestions') from exc


class TopicFeedbackInput(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


@router.post('/feedback', status_code=201)
async def save_topic_feedback(request: Request, body: TopicFeedbackInput):
    require_session(request, write=True)
    from uuid import uuid4
    settings, identity = context()
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, 'Describe what should change in at least five characters')
    async with bound_database(settings, identity) as db:
        await db.topic_feedback.insert_one({'_id': str(uuid4()), 'installation_id': identity,
            'reason': reason, 'created_at': datetime.now(timezone.utc)})
    return {'status': 'saved'}
