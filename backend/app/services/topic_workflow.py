"""Durable Phase 3 schedule, topic state and Telegram conversation."""

import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pymongo.errors import DuplicateKeyError

from app.services.topic_research import ResearchError, research_topics
from app.storage.jobs import claim_job, finish_job



TOPIC_SELECTION_STATES = {
    "AWAITING_TOPIC", "AWAITING_INPUT", "READY_FOR_DRAFT", "DRAFT_GENERATING",
    "DRAFT_REVIEW", "AWAITING_REVIEW", "DRAFT_FAILED", "PREVIEW_SENDING",
    "AWAITING_APPROVAL", "APPROVAL_VERIFIED", "PUBLISH_PENDING",
}


def is_topic_navigation(text: str) -> bool:
    upper = text.strip().upper()
    return upper == "TOPICS" or upper == "CHOOSE" or upper.startswith("CHOOSE ")

class TopicWorkflowError(ValueError):
    pass


def parse_owner_topic(text: str) -> str | None:
    match = re.fullmatch(r"MY TOPIC\s*:\s*(.*)", text.strip(), flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def next_occurrence(weekday: int, local_time: str, timezone_name: str,
                    after: datetime | None = None) -> datetime:
    if weekday not in range(7) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time):
        raise TopicWorkflowError("Choose a weekday and a time in HH:MM format")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise TopicWorkflowError("Choose a valid IANA timezone") from None
    after = after or now_utc()
    local = after.astimezone(zone)
    hour, minute = map(int, local_time.split(":"))
    for offset in range(0, 15):
        day = local.date() + timedelta(days=offset)
        if day.weekday() != weekday:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        # A nonexistent wall time moves to the first valid minute after the gap.
        for _ in range(180):
            roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
            if roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None):
                break
            candidate += timedelta(minutes=1)
        utc = candidate.astimezone(timezone.utc)
        if utc > after:
            return utc
    raise TopicWorkflowError("Could not calculate the next weekly invitation")


async def get_schedule(db, installation_id: str, default_timezone: str) -> dict:
    row = await db.weekly_schedules.find_one({"_id": "owner", "installation_id": installation_id})
    if not row:
        return {"enabled": False, "weekday": 0, "local_time": "09:00",
                "timezone": default_timezone, "next_at": None, "revision": 0,
                "paused": False}
    result = {key: row.get(key) for key in ("enabled", "weekday", "local_time",
                                         "timezone", "next_at", "revision")}
    # MongoDB returns UTC datetimes without tzinfo by default. Make the API timestamp
    # explicit so browsers outside UTC do not reinterpret it as their own wall time.
    if result["next_at"] and result["next_at"].tzinfo is None:
        result["next_at"] = result["next_at"].replace(tzinfo=timezone.utc)
    result["paused"] = bool(row.get("paused", False))
    return result


async def save_schedule(db, installation_id: str, *, expected_revision: int,
                        weekday: int, local_time: str, timezone_name: str,
                        enabled: bool) -> dict:
    calculated_next = next_occurrence(weekday, local_time, timezone_name)
    next_at = calculated_next if enabled else None
    current = await get_schedule(db, installation_id, timezone_name)
    if current["revision"] != expected_revision:
        raise TopicWorkflowError("Schedule changed; refresh before saving")
    update = await db.weekly_schedules.update_one(
        {"_id": "owner", "installation_id": installation_id, "revision": expected_revision},
        {"$set": {"enabled": enabled, "weekday": weekday, "local_time": local_time,
                  "timezone": timezone_name, "next_at": next_at, "paused": False,
                  "updated_at": now_utc()},
         "$inc": {"revision": 1}}, upsert=expected_revision == 0)
    if not update.matched_count and not update.upserted_id:
        raise TopicWorkflowError("Schedule changed; refresh before saving")
    return await get_schedule(db, installation_id, timezone_name)


async def set_schedule_paused(db, installation_id: str, default_timezone: str,
                              *, expected_revision: int, paused: bool) -> dict:
    current = await get_schedule(db, installation_id, default_timezone)
    if current["revision"] != expected_revision:
        raise TopicWorkflowError("Schedule changed; refresh before trying again")
    if paused and not current["enabled"]:
        raise TopicWorkflowError("Enable a weekly invitation schedule before pausing it")
    if not paused and not current["paused"]:
        raise TopicWorkflowError("The weekly invitation schedule is not paused")
    next_at = (None if paused else next_occurrence(current["weekday"],
        current["local_time"], current["timezone"]))
    result = await db.weekly_schedules.update_one(
        {"_id": "owner", "installation_id": installation_id, "revision": expected_revision,
         "enabled": current["enabled"], "paused": current["paused"]} if current["paused"] else
        {"_id": "owner", "installation_id": installation_id, "revision": expected_revision,
         "enabled": current["enabled"], "paused": {"$ne": True}},
        {"$set": {"enabled": not paused, "paused": paused, "next_at": next_at,
                  "updated_at": now_utc()}, "$inc": {"revision": 1}},
    )
    if result.modified_count != 1:
        raise TopicWorkflowError("Schedule changed; refresh before trying again")
    return await get_schedule(db, installation_id, default_timezone)


async def queue_message(db, workflow: dict, key: str, text: str, *, session=None) -> None:
    if len(text) > 3900:
        raise TopicWorkflowError("Telegram message exceeds the safe length limit")
    try:
        await db.messages.insert_one({"_id": key, "outbound_key": key,
            "channel": "telegram", "direction": "outbound", "kind": "phase3_topic",
            "workflow_id": workflow["_id"],
            "text": text, "status": "pending", "created_at": now_utc()}, session=session)
    except DuplicateKeyError:
        pass


async def start_workflow(db, installation_id: str, *, slot_key: str, origin: str) -> dict:
    if await db.workflows.find_one({"active": True}):
        raise TopicWorkflowError("Finish, skip, or discard the current conversation first")
    now = now_utc()
    workflow_id = str(uuid4())
    workflow = {"_id": workflow_id, "installation_id": installation_id,
        "kind": "weekly_topics", "short_code": "T" + workflow_id.split("-")[0].upper(),
        "slot_key": slot_key, "origin": origin, "state": "RESEARCH_PENDING", "active": True,
        "revision": 1, "shortlist_revision": 0, "shortlist": [],
        "selected_topic": None, "perspective": None, "experience": "unconfirmed",
        "created_at": now, "updated_at": now}
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                await db.workflows.insert_one(workflow, session=session)
                await db.jobs.insert_one({"_id": str(uuid4()), "schema_version": 1,
                    "kind": "research_topics", "dedupe_key": "research:" + workflow_id,
                    "payload": {"workflow_id": workflow_id}, "status": "pending",
                    "available_at": now, "attempt_count": 0, "revision": 1,
                    "created_at": now, "updated_at": now}, session=session)
    except DuplicateKeyError:
        raise TopicWorkflowError("Another conversation is already active") from None
    return workflow


async def current_workflow(db, installation_id: str) -> dict | None:
    row = await db.workflows.find_one({"active": True, "installation_id": installation_id})
    if not row or row.get("kind") != "weekly_topics":
        return None
    return {key: row.get(key) for key in ("_id", "short_code", "slot_key", "state", "revision",
            "shortlist_revision", "shortlist", "selected_topic", "perspective", "experience",
            "error", "draft_error", "publication_error", "post_url", "draft_version",
            "draft_id", "created_at", "updated_at", "invitation_sent_at",
            "input_step")}


async def schedule_tick(db, installation_id: str) -> None:
    schedule = await db.weekly_schedules.find_one({"_id": "owner", "installation_id": installation_id,
        "enabled": True, "paused": {"$ne": True}, "next_at": {"$lte": now_utc()}})
    if not schedule:
        return
    next_at = next_occurrence(schedule["weekday"], schedule["local_time"],
                              schedule["timezone"], now_utc())
    # Consolidate downtime into the most recent due slot, never a backlog.
    most_recent = next_occurrence(schedule["weekday"], schedule["local_time"],
                                  schedule["timezone"], now_utc() - timedelta(days=8))
    while most_recent <= now_utc():
        following = next_occurrence(schedule["weekday"], schedule["local_time"],
                                    schedule["timezone"], most_recent)
        if following > now_utc():
            break
        most_recent = following
    slot_key = most_recent.astimezone(ZoneInfo(schedule["timezone"])).strftime("%Y-%m-%d")
    active = await db.workflows.find_one({"active": True})
    if not active:
        try:
            active = await start_workflow(db, installation_id, slot_key=slot_key, origin="weekly")
        except TopicWorkflowError:
            active = await db.workflows.find_one({"active": True})
    result = await db.weekly_schedules.update_one(
        {"_id": "owner", "installation_id": installation_id,
         "revision": schedule["revision"], "next_at": schedule["next_at"]},
        {"$set": {"next_at": next_at, "last_slot": slot_key}, "$inc": {"revision": 1}})
    if result.modified_count != 1:
        return
    if active:
        if active.get("kind") == "weekly_topics" and active.get("slot_key") != slot_key:
            await queue_message(db, active, f"continue:{slot_key}",
                "Your earlier topic conversation is still open. Reply CONTINUE to see it, or DISCARD to end it.")


def format_shortlist(workflow: dict) -> str:
    parts = [f"Topic ideas (revision {workflow['shortlist_revision']}):"]
    for index, topic in enumerate(workflow["shortlist"], 1):
        url = topic["sources"][0]["url"]
        source = url if len(url) <= 240 else "See the dashboard for the full source link"
        explanation = f"\nMeaning: {topic['explanation'][:150]}" if topic.get('explanation') else ""
        takeaway = f"\nYou'll learn: {topic['reader_takeaway'][:150]}" if topic.get('reader_takeaway') else ""
        parts.append(f"{index}. {topic['title'][:100]}{explanation}{takeaway}\nWhy it fits: {topic['why_you'][:100]}\nSource: {source}")
    choice = ("CHOOSE <number>" if workflow["shortlist_revision"] == 1 else
              f"CHOOSE {workflow['shortlist_revision']}:<number>")
    parts.append(f"Reply {choice}, ALTERNATIVES, MY TOPIC: <idea>, or SKIP WEEK.")
    return "\n\n".join(parts)


async def run_research_job(db, installation_id: str, api_key: str, model: str,
                           worker_id: str) -> bool:
    # Leave research unclaimed while discovery runs: waiting must not consume retries or leases.
    if await db.jobs.find_one({"kind": "discover_trends", "payload.installation_id": installation_id,
                               "status": {"$in": ["pending", "running"]}}):
        return False
    job = await claim_job(db, kinds=["research_topics"], worker_id=worker_id, lease_seconds=300)
    if not job:
        return False
    workflow = await db.workflows.find_one({"_id": job["payload"]["workflow_id"],
                                            "active": True, "kind": "weekly_topics",
                                            "state": "RESEARCH_PENDING"})
    if not workflow:
        await finish_job(db, job_id=job["_id"], worker_id=worker_id,
                         revision=job["revision"], status="succeeded")
        return True
    started_at = now_utc()
    usage: dict = {}
    run_id = f"{job['_id']}:{job['attempt_count']}"
    try:
        dependency = job["payload"].get("discovery_job_id")
        if dependency:
            refresh = await db.jobs.find_one({"_id": dependency})
            if not refresh or refresh.get("status") != "succeeded":
                raise ResearchError("Source refresh failed. Refresh discovery sources again before regenerating suggestions.")
        topics = await research_topics(db, installation_id, api_key, model, usage)
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                await db.research_runs.update_one({"_id": run_id}, {"$setOnInsert": {
                    "job_id": job["_id"], "workflow_id": workflow["_id"],
                    "task": "propose_topics", "model": model, "status": "succeeded",
                    "started_at": started_at, "finished_at": now_utc(), "usage": usage}},
                    upsert=True, session=session)
                result = await db.workflows.update_one({"_id": workflow["_id"],
                    "state": "RESEARCH_PENDING", "active": True,
                    "shortlist_revision": workflow["shortlist_revision"]},
                    {"$set": {"state": "AWAITING_TOPIC", "shortlist": topics, "error": None,
                              "updated_at": now_utc()}, "$inc": {"revision": 1, "shortlist_revision": 1}},
                    session=session)
                if result.modified_count:
                    await queue_message(db, workflow,
                        f"invitation:{workflow['_id']}:{workflow['shortlist_revision'] + 1}",
                        "I have topic ideas for you. Reply TOPICS to see them, or MY TOPIC: <idea> to use your own topic.",
                        session=session)
        await finish_job(db, job_id=job["_id"], worker_id=worker_id,
                         revision=job["revision"], status="succeeded")
    except ResearchError as exc:
        if job["attempt_count"] < 3:
            await db.research_runs.update_one({"_id": run_id}, {"$setOnInsert": {
                "job_id": job["_id"], "workflow_id": workflow["_id"],
                "task": "propose_topics", "model": model, "status": "retry_wait",
                "started_at": started_at, "finished_at": now_utc(), "usage": usage}}, upsert=True)
            retry_at = now_utc() + timedelta(seconds=30 * (2 ** (job["attempt_count"] - 1))
                                              + secrets.randbelow(10))
            await db.jobs.update_one({"_id": job["_id"], "status": "running",
                "lease_owner": worker_id, "revision": job["revision"]},
                {"$set": {"status": "pending", "available_at": retry_at,
                          "updated_at": now_utc(), "error_code": "research_retry"},
                 "$unset": {"lease_owner": "", "lease_until": ""},
                 "$inc": {"revision": 1}})
            return True
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                await db.research_runs.update_one({"_id": run_id}, {"$setOnInsert": {
                    "job_id": job["_id"], "workflow_id": workflow["_id"],
                    "task": "propose_topics", "model": model, "status": "failed",
                    "started_at": started_at, "finished_at": now_utc(), "usage": usage}},
                    upsert=True, session=session)
                result = await db.workflows.update_one({"_id": workflow["_id"],
                    "state": "RESEARCH_PENDING", "active": True,
                    "shortlist_revision": workflow["shortlist_revision"]},
                    {"$set": {"state": "RESEARCH_FAILED", "error": str(exc),
                              "updated_at": now_utc()}, "$inc": {"revision": 1}}, session=session)
                if result.modified_count:
                    await queue_message(db, workflow,
                        f"research_failed:{workflow['_id']}:{workflow['revision']}",
                        "I couldn't find suitable sourced topics for your profile. Reply MY TOPIC: <idea> to continue, or SKIP WEEK.",
                        session=session)
        await finish_job(db, job_id=job["_id"], worker_id=worker_id,
                         revision=job["revision"], status="failed", error_code="research_unavailable")
    return True


async def reminder_tick(db, installation_id: str) -> None:
    workflow = await db.workflows.find_one({"installation_id": installation_id,
        "kind": "weekly_topics", "active": True, "state": "AWAITING_TOPIC",
        "invitation_sent_at": {"$lte": now_utc() - timedelta(hours=48)}})
    if workflow and not workflow.get("owner_replied_at"):
        await queue_message(db, workflow, f"reminder:{workflow['_id']}",
            "Your topic ideas are ready whenever you are. Reply TOPICS to see them, or SKIP WEEK.")


async def dispatch_topic_messages(db, telegram, installation_id: str) -> None:
    connection = await db.connections.find_one({"_id": "telegram", "installation_id": installation_id,
                                                "status": "connected"})
    if not connection:
        return
    while True:
        message = await db.messages.find_one_and_update(
            {"direction": "outbound", "kind": "phase3_topic", "status": "pending"},
            {"$set": {"status": "sending", "sending_at": now_utc()}},
            sort=[("created_at", 1)])
        if not message:
            return
        workflow = (await db.workflows.find_one({"_id": message["workflow_id"]})
                    if message.get("workflow_id") else None)
        if not message.get("standalone_owner_notice") and (
                not workflow or workflow.get("kind") != "weekly_topics"):
            await db.messages.update_one({"_id": message["_id"], "status": "sending"},
                {"$set": {"status": "cancelled"}})
            continue
        try:
            provider_id = await telegram.send_text(connection["chat_id"], message["text"])
        except Exception:
            await db.messages.update_one({"_id": message["_id"], "status": "sending"},
                {"$set": {"status": "send_unknown", "updated_at": now_utc()}})
            return
        sent_at = now_utc()
        await db.messages.update_one({"_id": message["_id"], "status": "sending"},
            {"$set": {"status": "accepted", "provider_message_id": provider_id,
                      "accepted_at": sent_at}})
        if message["_id"].startswith("invitation:"):
            await db.workflows.update_one({"_id": workflow["_id"]},
                {"$set": {"invitation_sent_at": sent_at}})


def continuation_recap(workflow: dict, now: datetime | None = None) -> str:
    """Describe saved progress without implying that unsaved Telegram input was recovered."""
    now = now or now_utc()
    state = workflow["state"]
    topic = (workflow.get("selected_topic") or {}).get("title")
    lines = [f"Saved conversation: {topic or 'topic selection'}."]
    if state == "AWAITING_TOPIC":
        lines.append("Reply TOPICS to see the saved shortlist, or MY TOPIC: <idea>.")
    elif state == "AWAITING_INPUT":
        if workflow.get("input_step") == "experience":
            lines.append("Your perspective is saved. Reply HANDS ON, EXPLORING, or SKIP INPUT.")
        else:
            lines.append("Your topic is saved. Share your view, or reply SKIP INPUT.")
    elif state in {"RESEARCH_PENDING", "RESEARCH_FAILED"}:
        lines.append("Topic research is pending or needs attention. Reply TOPICS later, or MY TOPIC: <idea>.")
    elif state == "READY_FOR_DRAFT":
        lines.append("Your topic and input are saved. Reply DRAFT to create a draft.")
    elif state == "DRAFT_GENERATING":
        lines.append("A draft request is running. Wait for its result before reviewing.")
    elif state in {"DRAFT_REVIEW", "AWAITING_REVIEW", "DRAFT_FAILED"}:
        lines.append(f"Draft V{workflow.get('draft_version', 0)} is saved. Reply DRAFT to retry, "
                     "send feedback, or reply FINAL for a fresh exact preview.")
    elif state in {"AWAITING_APPROVAL", "PUBLISH_PENDING", "PREVIEW_SENDING"}:
        lines.append(f"Draft V{workflow.get('draft_version', 0)} is saved. "
                     "The earlier approval is no longer usable. Reply FINAL for a fresh preview.")
    elif state in {"PUBLISHING", "PUBLISH_UNKNOWN"}:
        lines.append("A LinkedIn request may already be in progress or uncertain. "
                     "Check the dashboard before taking further action.")
    else:
        lines.append("Your saved progress is available in the dashboard.")
    updated = workflow.get("updated_at")
    if updated and (now - (updated.replace(tzinfo=timezone.utc)
                           if updated.tzinfo is None else updated)) >= timedelta(days=7):
        lines.append("This conversation is over a week old. Review time-sensitive claims and sources before FINAL.")
    lines.append("Messages sent while the bot was offline may need to be resent if Telegram did not deliver them.")
    return "\n".join(lines)


async def handle_continue_message(db, message: dict, installation_id: str) -> str | None:
    if message["text"].strip().upper() != "CONTINUE":
        return None
    workflow = await db.workflows.find_one({"active": True, "installation_id": installation_id,
                                            "kind": "weekly_topics"})
    if not workflow:
        now = now_utc()
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                changed = await db.messages.update_one({"_id": message["_id"],
                    "status": "accepted_unprocessed"},
                    {"$set": {"status": "processed", "processed_at": now}}, session=session)
                if changed.modified_count != 1:
                    return "already_processed"
                await db.messages.insert_one({"_id": f"continue-empty:{message['_id']}",
                    "channel": "telegram", "direction": "outbound",
                    "kind": "phase3_topic", "standalone_owner_notice": True,
                    "outbound_key": f"continue-empty:{message['_id']}",
                    "text": "There is no active conversation. Start a new topic from the dashboard; "
                            "your previous posts and drafts are in Recent outcomes.",
                    "status": "pending", "created_at": now}, session=session)
        return "no_active_conversation"
    now = now_utc()
    invalidates = workflow["state"] in {"AWAITING_APPROVAL", "PUBLISH_PENDING", "PREVIEW_SENDING"}
    recap = continuation_recap(workflow, now)
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            current = await db.messages.find_one({"_id": message["_id"],
                "status": "accepted_unprocessed"}, session=session)
            if not current:
                return "already_processed"
            if invalidates:
                changed = await db.workflows.update_one({"_id": workflow["_id"],
                    "revision": workflow["revision"], "state": workflow["state"]},
                    {"$set": {"state": "AWAITING_REVIEW", "pending_preview.status": "invalidated_by_owner",
                              "updated_at": now}, "$inc": {"revision": 1}}, session=session)
                if changed.modified_count != 1:
                    return "workflow_changed"
                await db.jobs.update_many({"kind": "publish", "workflow_id": workflow["_id"],
                    "status": "pending"}, {"$set": {"status": "cancelled_by_owner", "updated_at": now}},
                    session=session)
                await db.messages.update_many({"kind": "phase4_preview",
                    "preview_id": workflow.get("preview_id"), "status": "pending"},
                    {"$set": {"status": "cancelled", "updated_at": now}}, session=session)
                if workflow.get("approval_id"):
                    await db.approval_receipts.update_one({"_id": workflow["approval_id"],
                        "status": "pending_publication"},
                        {"$set": {"status": "cancelled_by_owner"}}, session=session)
            await db.messages.update_one({"_id": message["_id"], "status": "accepted_unprocessed"},
                {"$set": {"status": "processed", "processed_at": now,
                          "workflow_id": workflow["_id"]}}, session=session)
            await queue_message(db, workflow, f"topic_reply:{message['_id']}", recap, session=session)
    return "continued"


async def handle_topic_message(db, message: dict, installation_id: str) -> str | None:
    """Consume one accepted owner message, preserving explicit, versioned choices."""
    workflow = await db.workflows.find_one({"active": True, "installation_id": installation_id,
                                            "kind": "weekly_topics"})
    if not workflow:
        return None
    text = message["text"].strip()
    upper = text.upper()
    state = workflow["state"]
    reply = None
    updates = {"owner_replied_at": now_utc(), "updated_at": now_utc()}
    next_state = None
    if upper in {"SKIP WEEK", "DISCARD"}:
        next_state = "SKIPPED" if upper == "SKIP WEEK" else "DISCARDED"
        updates["active"] = False
        reply = "This week's topic conversation is closed. No post was created or published."
    elif upper == "TOPICS" and state in TOPIC_SELECTION_STATES:
        reply = format_shortlist(workflow) if workflow.get("shortlist") else "No saved suggestions are available. Start a new topic conversation from the dashboard."
    elif upper == "CONTINUE" and state == "AWAITING_TOPIC":
        reply = format_shortlist(workflow)
    elif upper == "CONTINUE" and state == "AWAITING_INPUT":
        reply = "What is your view on this topic? Share a practical observation, concern, or example. You can also reply SKIP INPUT."
    elif upper == "ALTERNATIVES" and state in {"AWAITING_TOPIC", "AWAITING_INPUT", "RESEARCH_FAILED"}:
        next_state = "RESEARCH_PENDING"
        updates["rejected_titles"] = (workflow.get("rejected_titles", []) +
                                       [topic["title"] for topic in workflow["shortlist"]])[-50:]
        updates["shortlist"] = []
        updates["selected_topic"] = None
        updates["perspective"] = None
        updates["experience"] = "unconfirmed"
        reply = "I’ll look for a new set of sourced ideas. Reply TOPICS when they are ready."
    elif (idea := parse_owner_topic(text)) is not None and state in {"AWAITING_TOPIC", "RESEARCH_FAILED", "RESEARCH_PENDING"}:
        if not 5 <= len(idea) <= 300:
            reply = "Please send MY TOPIC: followed by an idea of 5 to 300 characters (for example, MY TOPIC: Java development)."
        else:
            updates["selected_topic"] = {"title": idea, "owner_proposed": True,
                                           "sources": [], "angle": "Owner supplied topic"}
            updates["input_step"] = "perspective"
            next_state = "AWAITING_INPUT"
            reply = "What is your view on this topic? Share a practical observation, concern, or example. You can also reply SKIP INPUT."
    elif state in TOPIC_SELECTION_STATES and (
        (match := re.fullmatch(r"CHOOSE (\d+):(\d+)", upper)) or
        (short_match := re.fullmatch(r"(?:CHOOSE )?(\d+)", upper))
    ):
        revision, number = (map(int, match.groups()) if match else
                            (workflow["shortlist_revision"], int(short_match.group(1))))
        if not match and workflow["shortlist_revision"] != 1:
            reply = (f"Please use CHOOSE {workflow['shortlist_revision']}:<number> "
                     "for this refreshed list. Reply TOPICS to see it again.")
        elif revision != workflow["shortlist_revision"] or not 1 <= number <= len(workflow["shortlist"]):
            reply = "That choice is out of date. Reply TOPICS to see the current list."
        elif workflow.get("selected_topic") == workflow["shortlist"][number - 1]:
            reply = f"That topic is already selected: {workflow['selected_topic']['title']}. Your current progress is saved. Reply CONTINUE to see the next step."
        else:
            updates["selected_topic"] = workflow["shortlist"][number - 1]
            updates["topic_selection_revision"] = workflow.get("topic_selection_revision", 1) + 1
            updates.update({"draft_id": None, "generation_id": None, "generation_feedback": None,
                            "draft_error": None, "pending_preview": None, "preview_id": None,
                            "approval_id": None, "envelope_hash": None, "publication_error": None})
            updates["perspective"] = None
            updates["experience"] = "unconfirmed"
            updates["input_step"] = "perspective"
            next_state = "AWAITING_INPUT"
            reply = (f"Selected: {updates['selected_topic']['title']}\n"
                     "What is your view on this topic? Share a practical observation, concern, or example. You can also reply SKIP INPUT.")
    elif is_topic_navigation(text) and state in TOPIC_SELECTION_STATES:
        reply = "Use CHOOSE <number>, or CHOOSE <list revision>:<number> for a refreshed list. Reply TOPICS to see your suggestions."
    elif state == "AWAITING_INPUT" and upper in {"SKIP INPUT", "DRAFT"}:
        next_state = "READY_FOR_DRAFT"
        reply = "Saved. Reply DRAFT to create the first draft. No post has been created or published yet."
    elif state == "AWAITING_INPUT" and workflow.get("input_step") == "perspective":
        if not 1 <= len(text) <= 2000:
            reply = "Please send up to 2,000 characters, or reply SKIP INPUT."
        else:
            updates["perspective"] = text
            updates["input_step"] = "experience"
            reply = "Have you used this yourself? Reply HANDS ON, EXPLORING, or SKIP INPUT."
    elif state == "AWAITING_INPUT" and workflow.get("input_step") == "experience":
        if upper in {"HANDS ON", "EXPLORING"}:
            updates["experience"] = "hands_on" if upper == "HANDS ON" else "exploring"
            next_state = "READY_FOR_DRAFT"
            reply = "Saved. Reply DRAFT to create the first draft from your topic and perspective. Nothing has been published."
        else:
            reply = "Please reply HANDS ON, EXPLORING, or SKIP INPUT."
    elif state == "RESEARCH_PENDING":
        reply = "I’m still collecting topic ideas. You can reply MY TOPIC: <idea> or SKIP WEEK."
    elif state == "RESEARCH_FAILED":
        reply = "Research needs attention. You can reply MY TOPIC: <idea> or SKIP WEEK."
    elif state == "READY_FOR_DRAFT":
        reply = "Your input is saved. Reply DRAFT to create a draft, CHOOSE <number> to switch topic, or DISCARD to close this conversation."
    if reply is None:
        return None
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            current = await db.messages.find_one({"_id": message["_id"],
                "status": "accepted_unprocessed"}, session=session)
            if not current:
                return "already_processed"
            changes = {"$set": updates, "$inc": {"revision": 1}}
            if next_state:
                updates["state"] = next_state
            result = await db.workflows.update_one({"_id": workflow["_id"],
                "revision": workflow["revision"], "state": state}, changes, session=session)
            if result.modified_count != 1:
                return None
            await db.messages.update_many({"kind": "phase3_topic",
                "workflow_id": workflow["_id"], "status": "pending",
                "_id": {"$regex": "^(reminder|continue):"}},
                {"$set": {"status": "cancelled", "updated_at": now_utc()}},
                session=session)
            if next_state in {"SKIPPED", "DISCARDED", "RESEARCH_PENDING", "AWAITING_INPUT"}:
                await db.messages.update_many({"kind": "phase3_topic",
                    "workflow_id": workflow["_id"], "status": "pending"},
                    {"$set": {"status": "cancelled", "updated_at": now_utc()}},
                    session=session)
            if "topic_selection_revision" in updates:
                # The workflow revision fences already-running generation/publication tasks.
                await db.jobs.update_many({"kind": "generate_draft", "payload.workflow_id": workflow["_id"],
                    "status": "pending"}, {"$set": {"status": "cancelled_by_owner", "updated_at": now_utc()}}, session=session)
                await db.jobs.update_many({"kind": "publish", "workflow_id": workflow["_id"],
                    "status": "pending"}, {"$set": {"status": "cancelled_by_owner", "updated_at": now_utc()}}, session=session)
                if workflow.get("approval_id"):
                    await db.approval_receipts.update_one({"_id": workflow["approval_id"], "status": "pending_publication"},
                        {"$set": {"status": "cancelled_by_owner"}}, session=session)
                await db.messages.update_many({"workflow_id": workflow["_id"], "direction": "outbound",
                    "kind": {"$in": ["phase3_topic", "phase4_preview"]}, "status": "pending"},
                    {"$set": {"status": "cancelled", "updated_at": now_utc()}}, session=session)
            await queue_message(db, workflow, f"topic_reply:{message['_id']}", reply, session=session)
            await db.messages.update_one({"_id": message["_id"]},
                {"$set": {"status": "processed", "processed_at": now_utc(),
                          "workflow_id": workflow["_id"]}}, session=session)
            if next_state == "RESEARCH_PENDING":
                await db.jobs.insert_one({"_id": str(uuid4()), "schema_version": 1,
                    "kind": "research_topics", "dedupe_key": f"research:{workflow['_id']}:{workflow['shortlist_revision'] + 1}",
                    "payload": {"workflow_id": workflow["_id"]}, "status": "pending",
                    "available_at": now_utc(), "attempt_count": 0, "revision": 1,
                    "created_at": now_utc(), "updated_at": now_utc()}, session=session)
    return "topic_processed"


async def regenerate_topics(db, installation_id: str, expected_revision: int) -> dict:
    """Replace unselected suggestions; preserve conversations already authoring a post."""
    workflow = await db.workflows.find_one({'installation_id': installation_id, 'active': True,
        'kind': 'weekly_topics'})
    if (not workflow or workflow['state'] not in {'AWAITING_TOPIC', 'RESEARCH_FAILED'}
            or workflow.get('selected_topic') or workflow['revision'] != expected_revision):
        raise TopicWorkflowError('Conversation changed; refresh before regenerating suggestions')
    now = now_utc()
    discovery = await db.jobs.find_one({'kind': 'discover_trends', 'payload.installation_id': installation_id},
                                      sort=[('created_at', -1)])
    payload = {'workflow_id': workflow['_id']}
    if discovery and discovery.get('status') in {'pending', 'running', 'failed'}:
        payload['discovery_job_id'] = discovery['_id']
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            result = await db.workflows.update_one({'_id': workflow['_id'], 'active': True,
                'state': workflow['state'], 'revision': expected_revision},
                {'$set': {'state': 'RESEARCH_PENDING', 'shortlist': [], 'error': None, 'updated_at': now},
                 '$inc': {'revision': 1}}, session=session)
            if result.modified_count != 1:
                raise TopicWorkflowError('Conversation changed; refresh before regenerating suggestions')
            await db.jobs.insert_one({'_id': str(uuid4()), 'schema_version': 1,
                'kind': 'research_topics', 'dedupe_key': f"dashboard-research:{workflow['_id']}:{expected_revision}",
                'payload': payload, 'status': 'pending',
                'available_at': now, 'attempt_count': 0, 'revision': 1,
                'created_at': now, 'updated_at': now}, session=session)
    return {'id': workflow['_id'], 'state': 'RESEARCH_PENDING'}
