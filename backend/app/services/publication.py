"""Guarded, one-shot publication of a durably approved envelope."""

from datetime import datetime, timezone

import keyring
from keyring.errors import KeyringError
from pymongo.errors import PyMongoError

from app.domain.approval import PublicationEnvelope
from app.services.draft_context import blocked_terms
from app.integrations.linkedin_posts import (
    PostResult, create_text_post, post_permalink, text_post_payload,
)
from app.storage.linkedin_connection import _service
from app.storage.publication_journal import load_success, remove_success, save_success


class PublicationError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _preflight(db, installation_id: str, bot_id: str, lease_token: str, job: dict):
    now = datetime.now(timezone.utc)
    workflow = await db.workflows.find_one({"_id": job["workflow_id"]})
    approval = await db.approval_receipts.find_one({"_id": job["approval_id"]})
    telegram = await db.connections.find_one({"_id": "telegram"})
    linkedin = await db.connections.find_one({"_id": "linkedin"})
    receiver = await db.telegram_receivers.find_one({"_id": bot_id})
    if (not workflow or workflow.get("state") != "PUBLISH_PENDING"
            or workflow.get("approval_id") != job["approval_id"]
            or not approval or approval.get("status") != "pending_publication"
            or not telegram or telegram.get("installation_id") != installation_id
            or telegram.get("status") != "connected" or telegram.get("bot_id") != bot_id
            or not linkedin or linkedin.get("installation_id") != installation_id
            or linkedin.get("status") != "connected"
            or not receiver or receiver.get("installation_id") != installation_id
            or receiver.get("lease_token") != lease_token
            or not receiver.get("lease_until") or _utc(receiver["lease_until"]) <= now
            or receiver.get("approval_barrier") is not False
            or receiver.get("connection_epoch") != workflow["pending_preview"]["receiver_epoch"]
            or telegram.get("binding_revision") != workflow["pending_preview"]["binding_revision"]
            or linkedin.get("member_id") != workflow.get("linkedin_member_id")
            or linkedin.get("binding_revision", 0) != workflow.get("linkedin_connection_revision", 0)
            or (linkedin.get("expires_at") and _utc(linkedin["expires_at"]) <= now)):
        raise PublicationError("Publication preflight found a changed or unavailable connection")
    control = await db.runtime_control.find_one({"_id": "publication"})
    if control and control.get("blocked"):
        raise PublicationError("Publication is blocked for recovery")
    earlier_input = await db.messages.find_one({
        "channel": "telegram", "direction": "inbound", "status": "accepted_unprocessed"
    })
    if earlier_input:
        raise PublicationError("Earlier owner input must be processed before publication")
    later_input = await db.messages.find_one({
        "channel": "telegram", "direction": "inbound",
        "ingress_seq": {"$gt": approval.get("approval_ingress_seq", 0)},
    })
    if later_input:
        raise PublicationError("Owner sent another message after approval; fresh review is required")
    envelope = PublicationEnvelope.model_validate(approval["envelope"])
    if (envelope.digest() != approval.get("envelope_hash")
            or envelope.digest() != workflow.get("envelope_hash")
            or envelope.author_urn != f"urn:li:person:{linkedin['member_id']}"):
        raise PublicationError("Approved envelope no longer matches the destination")
    text_post_payload(envelope)
    profile_collection = getattr(db, "owner_profiles", None)
    profile = await profile_collection.find_one({"_id": "profile", "installation_id": installation_id}) if profile_collection else None
    if profile and blocked_terms(envelope.text, profile["data"]):
        raise PublicationError("Approved post conflicts with a saved publication boundary")
    try:
        token = keyring.get_password(_service(installation_id), linkedin["secret_ref"])
    except (KeyError, KeyringError) as exc:
        raise PublicationError("LinkedIn credential is unavailable") from exc
    if not token:
        raise PublicationError("LinkedIn credential is unavailable")
    return workflow, approval, envelope, token


async def _record_result(db, job: dict, result: PostResult) -> None:
    now = datetime.now(timezone.utc)
    attempt_id = f"attempt:{job['approval_id']}"
    if result.outcome == "confirmed":
        workflow_state, attempt_state, job_state = "PUBLISHED", "confirmed", "succeeded"
        notice = f"Published to LinkedIn: {post_permalink(result.post_id)}"
    elif result.outcome == "definitive_failure":
        workflow_state, attempt_state, job_state = "AWAITING_REVIEW", "definitive_failure", "failed"
        notice = "LinkedIn rejected the post. Your draft is preserved; a fresh preview is required."
    else:
        workflow_state, attempt_state, job_state = "PUBLISH_UNKNOWN", "unknown", "blocked"
        notice = "LinkedIn publication outcome is uncertain. Do not retry; check your LinkedIn profile."
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            attempt = await db.publication_attempts.update_one(
                {"_id": attempt_id, "status": "PUBLISHING"},
                {"$set": {"status": attempt_state, "post_id": result.post_id,
                          "http_status": result.status_code, "result_reason": result.reason,
                          "resolved_at": now}}, session=session,
            )
            if attempt.modified_count != 1:
                raise PublicationError("Publication attempt changed before result commit")
            workflow = await db.workflows.update_one(
                {"_id": job["workflow_id"], "state": "PUBLISHING",
                 "approval_id": job["approval_id"]},
                {"$set": {"state": workflow_state,
                          "active": workflow_state != "PUBLISHED",
                          "post_id": result.post_id,
                          "post_url": post_permalink(result.post_id) if result.post_id else None,
                          "updated_at": now}, "$inc": {"revision": 1}}, session=session,
            )
            if workflow.modified_count != 1:
                raise PublicationError("Workflow changed before publication result commit")
            finished = await db.jobs.update_one(
                {"_id": job["_id"], "status": "running"},
                {"$set": {"status": job_state, "finished_at": now}}, session=session,
            )
            if finished.modified_count != 1:
                raise PublicationError("Publication job changed before result commit")
            await db.approval_receipts.update_one(
                {"_id": job["approval_id"]},
                {"$set": {"status": attempt_state}}, session=session,
            )
            original = await db.workflows.find_one({"_id": job["workflow_id"]}, session=session)
            pending = original["pending_preview"]
            await db.messages.insert_one(
                {"_id": f"publication-notice:{attempt_id}",
                 "channel": "telegram", "direction": "outbound",
                 "outbound_key": f"publication-notice:{attempt_id}",
                 "bot_id": pending["bot_id"], "chat_id": pending["chat_id"],
                 "binding_revision": pending["binding_revision"],
                 "text": notice, "status": "pending", "created_at": now},
                session=session,
            )


async def publish_one(
    db, settings, installation_id: str, bot_id: str, lease_token: str,
    *, post_sender=create_text_post,
) -> str | None:
    """Cross the durable send boundary once, then classify the single provider response."""
    if not settings.publishing_enabled:
        return None
    job = await db.jobs.find_one({"kind": "publish", "status": "pending"},
                                 sort=[("created_at", 1)])
    if not job:
        return None
    workflow, approval, envelope, token = await _preflight(
        db, installation_id, bot_id, lease_token, job
    )
    attempt_id = f"attempt:{job['approval_id']}"
    now = datetime.now(timezone.utc)
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            sequence = await db.owner_settings.update_one(
                {"_id": "owner", "installation_id": installation_id,
                 "next_event_seq": approval["approval_ingress_seq"]},
                {"$inc": {"publication_epoch": 1}}, session=session,
            )
            if sequence.modified_count != 1:
                raise PublicationError("Owner input changed before publication send boundary")
            if await db.publication_attempts.find_one({"_id": attempt_id}, session=session):
                raise PublicationError("An attempt already exists; never send twice")
            await db.publication_attempts.insert_one(
                {"_id": attempt_id, "workflow_id": workflow["_id"],
                 "approval_id": approval["_id"], "envelope_hash": envelope.digest(),
                 "status": "PUBLISHING", "send_started_at": now,
                 "receiver_epoch": workflow["pending_preview"]["receiver_epoch"],
                 "worker_lease_token": lease_token},
                session=session,
            )
            transition = await db.workflows.update_one(
                {"_id": workflow["_id"], "state": "PUBLISH_PENDING",
                 "revision": workflow["revision"]},
                {"$set": {"state": "PUBLISHING", "updated_at": now},
                 "$inc": {"revision": 1}}, session=session,
            )
            if transition.modified_count != 1:
                raise PublicationError("Workflow changed before send boundary")
            claimed = await db.jobs.update_one(
                {"_id": job["_id"], "status": "pending"},
                {"$set": {"status": "running", "started_at": now}}, session=session,
            )
            if claimed.modified_count != 1:
                raise PublicationError("Publication job was already claimed")
    receiver = await db.telegram_receivers.find_one({"_id": bot_id})
    attempt = await db.publication_attempts.find_one({"_id": attempt_id})
    telegram = await db.connections.find_one({"_id": "telegram"})
    linkedin = await db.connections.find_one({"_id": "linkedin"})
    control = await db.runtime_control.find_one({"_id": "publication"})
    if (not receiver or receiver.get("lease_token") != lease_token
            or not receiver.get("lease_until")
            or _utc(receiver["lease_until"]) <= datetime.now(timezone.utc)
            or receiver.get("approval_barrier") is not False
            or receiver.get("connection_epoch") != workflow["pending_preview"]["receiver_epoch"]
            or not attempt or attempt.get("status") != "PUBLISHING"
            or not telegram or telegram.get("bot_id") != bot_id
            or telegram.get("binding_revision") != workflow["pending_preview"]["binding_revision"]
            or not linkedin or linkedin.get("member_id") != workflow["linkedin_member_id"]
            or linkedin.get("binding_revision", 0) != workflow.get("linkedin_connection_revision", 0)
            or (control and control.get("blocked"))):
        result = PostResult("unknown", None, None, "ownership_lost_after_send_boundary")
    else:
        result = await post_sender(token, settings.linkedin_api_version, envelope)
    if result.outcome == "confirmed":
        save_success(settings.app_data_dir, attempt_id, result.post_id)
    await _record_result(db, job, result)
    if result.outcome == "confirmed":
        remove_success(settings.app_data_dir, attempt_id)
    return result.outcome


async def recover_inflight(db, data_dir) -> int:
    """Never resend a PUBLISHING attempt after restart."""
    attempts = await db.publication_attempts.find({"status": "PUBLISHING"}).to_list(length=100)
    for attempt in attempts:
        job = await db.jobs.find_one({"approval_id": attempt["approval_id"]})
        if not job:
            raise PublicationError("In-flight attempt has no publication job")
        post_id = load_success(data_dir, attempt["_id"])
        result = (
            PostResult("confirmed", 201, post_id, "journal_recovery") if post_id
            else PostResult("unknown", None, None, "restart_after_send_boundary")
        )
        await _record_result(db, job, result)
        if post_id:
            remove_success(data_dir, attempt["_id"])
    return len(attempts)


async def dispatch_publication_notice(db, telegram_client, bot_id: str) -> int:
    notices = await db.messages.find(
        {"direction": "outbound", "status": "pending",
         "outbound_key": {"$regex": "^publication-notice:"}}
    ).to_list(length=20)
    sent = 0
    for notice in notices:
        connection = await db.connections.find_one({"_id": "telegram"})
        if (not connection or connection.get("bot_id") != bot_id
                or connection.get("chat_id") != notice["chat_id"]
                or connection.get("binding_revision") != notice["binding_revision"]):
            continue
        claim = await db.messages.update_one(
            {"_id": notice["_id"], "status": "pending"},
            {"$set": {"status": "sending", "attempt_started_at": datetime.now(timezone.utc)}},
        )
        if claim.modified_count != 1:
            continue
        try:
            message_id = await telegram_client.send_text(notice["chat_id"], notice["text"])
        except Exception:
            await db.messages.update_one(
                {"_id": notice["_id"], "status": "sending"},
                {"$set": {"status": "delivery_unknown"}},
            )
            continue
        await db.messages.update_one(
            {"_id": notice["_id"], "status": "sending"},
            {"$set": {"status": "accepted", "provider_message_id": message_id,
                      "accepted_at": datetime.now(timezone.utc)}},
        )
        sent += 1
    return sent
