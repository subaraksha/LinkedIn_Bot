"""Serialize saved owner messages into approval decisions; never call LinkedIn here."""

from dataclasses import fields
from datetime import datetime, timezone

from pydantic import ValidationError

from app.domain.approval import PublicationEnvelope, parse_publish_command
from app.domain.preview import PendingPreview, approve_preview
from app.domain.telegram import InboundText, OwnerBinding
from app.services.topic_workflow import handle_continue_message, handle_topic_message
from app.services.draft_workflow import handle_draft_message, validate_phase4_command


class ApprovalIntakeError(RuntimeError):
    pass


def _pending_preview(record: dict) -> PendingPreview:
    names = {field.name for field in fields(PendingPreview)}
    prepared = {name: value for name, value in record.items() if name in names}
    expiry = prepared.get("expires_at")
    if isinstance(expiry, datetime) and expiry.tzinfo is None:
        prepared["expires_at"] = expiry.replace(tzinfo=timezone.utc)
    return PendingPreview(**prepared)


async def process_next_owner_message(db, installation_id: str, publishing_enabled: bool) -> str | None:
    """Process one accepted message in ingress order. Returns a safe disposition."""
    message = await db.messages.find_one(
        {"channel": "telegram", "direction": "inbound", "status": "accepted_unprocessed"},
        sort=[("ingress_seq", 1), ("received_at", 1)],
    )
    if not message:
        return None
    command = parse_publish_command(message["text"])
    if command is None:
        continued = await handle_continue_message(db, message, installation_id)
        if continued is not None:
            return continued
        draft_result = await handle_draft_message(db, message, installation_id,
                                                  publishing_enabled=publishing_enabled)
        if draft_result is not None:
            return draft_result
        topic_result = await handle_topic_message(db, message, installation_id)
        if topic_result is not None:
            return topic_result
        await db.messages.update_one(
            {"_id": message["_id"], "status": "accepted_unprocessed"},
            {"$set": {"status": "unhandled", "processed_at": datetime.now(timezone.utc)}},
        )
        return "unhandled"
    phase4_result = await validate_phase4_command(db, message, installation_id,
                                                  publishing_enabled=publishing_enabled)
    if phase4_result is not None:
        return phase4_result
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            current = await db.messages.find_one(
                {"_id": message["_id"], "status": "accepted_unprocessed"}, session=session
            )
            if not current:
                return "already_processed"
            telegram = await db.connections.find_one({"_id": "telegram"}, session=session)
            linkedin = await db.connections.find_one({"_id": "linkedin"}, session=session)
            workflow = await db.workflows.find_one(
                {"short_code": command.workflow_code, "active": True}, session=session
            )
            reason = None
            receipt = None
            if not publishing_enabled:
                reason = "publication_disabled"
            elif (not telegram or telegram.get("installation_id") != installation_id
                  or telegram.get("status") != "connected"):
                reason = "telegram_binding_changed"
            elif (not linkedin or linkedin.get("installation_id") != installation_id
                  or linkedin.get("status") != "connected"):
                reason = "linkedin_connection_changed"
            elif not workflow or workflow.get("state") != "AWAITING_APPROVAL":
                reason = "no_active_preview"
            else:
                intervening_input = await db.messages.find_one(
                    {"channel": "telegram", "direction": "inbound",
                     "ingress_seq": {"$gt": workflow.get("preview_issued_after_seq", 0),
                                     "$lt": current.get("ingress_seq", 0)}},
                    session=session,
                )
                pending_record = workflow.get("pending_preview")
                draft = await db.draft_versions.find_one(
                    {"_id": workflow.get("draft_id")}, session=session
                )
                receiver = await db.telegram_receivers.find_one(
                    {"_id": telegram.get("bot_id")}, session=session
                )
                parts = await db.messages.find(
                    {"workflow_id": workflow["_id"], "direction": "outbound"},
                    session=session,
                ).sort("part_index", 1).to_list(length=100)
                if intervening_input:
                    reason = "intervening_owner_input"
                elif current.get("ingress_seq", 0) <= workflow.get("preview_issued_after_seq", 0):
                    reason = "stale_owner_message"
                elif (not pending_record or pending_record.get("status") != "active"
                        or not draft or not receiver or len(parts) != 3
                        or [part.get("part_index") for part in parts] != [0, 1, 2]):
                    reason = "preview_incomplete"
                elif any(part.get("status") != "accepted" for part in parts):
                    reason = "preview_delivery_unconfirmed"
                elif linkedin.get("member_id") != workflow.get("linkedin_member_id"):
                    reason = "linkedin_account_changed"
                elif linkedin.get("binding_revision", 0) != workflow.get("linkedin_connection_revision", 0):
                    reason = "linkedin_connection_changed"
                elif (current.get("bot_id") != telegram.get("bot_id")
                      or current.get("binding_revision") != telegram.get("binding_revision")
                      or current.get("receiver_epoch") != receiver.get("connection_epoch")):
                    reason = "stale_owner_message"
                else:
                    try:
                        pending = _pending_preview(pending_record)
                        envelope = PublicationEnvelope.model_validate(draft["envelope"])
                    except (TypeError, KeyError, ValidationError):
                        reason = "preview_invalid"
                    else:
                        if (draft.get("body") != envelope.text
                                or draft.get("envelope_hash") != envelope.digest()
                                or workflow.get("envelope_hash") != envelope.digest()
                                or parts[1].get("text") != envelope.text
                                or f"PUBLISH {pending.workflow_code} V{pending.draft_version} {pending.challenge_code}"
                                not in (parts[2].get("text") or "")):
                            reason = "draft_changed"
                        if reason:
                            receipt = None
                        else:
                            binding = OwnerBinding(
                                bot_id=telegram["bot_id"], sender_id=telegram["sender_id"],
                                chat_id=telegram["chat_id"], revision=telegram["binding_revision"],
                            )
                            inbound = InboundText(
                                update_id=int(current["provider_event_id"]),
                                message_id=current["provider_message_id"],
                                sender_id=current["sender_id"], chat_id=current["chat_id"],
                                text=current["text"],
                            )
                            receipt = approve_preview(
                                pending, inbound, binding,
                                receiver.get("connection_epoch", 0), envelope,
                            )
                            if receipt is None:
                                reason = "approval_mismatch"
            now = datetime.now(timezone.utc)
            if reason:
                changed = await db.messages.update_one(
                    {"_id": current["_id"], "status": "accepted_unprocessed"},
                    {"$set": {"status": "rejected_approval", "reason": reason,
                              "processed_at": now}}, session=session,
                )
                if changed.modified_count != 1:
                    raise ApprovalIntakeError("Approval input changed during processing")
                return reason
            receipt_id = f"approval:{workflow['_id']}"
            await db.approval_receipts.insert_one(
                {"_id": receipt_id, "workflow_id": workflow["_id"],
                 "source_message_id": current["_id"],
                 "draft_id": workflow["draft_id"],
                 "draft_version": receipt.draft_version,
                 "approval_ingress_seq": current.get("ingress_seq", 0),
                 "envelope_hash": receipt.envelope_hash,
                 "envelope": envelope.model_dump(mode="json"),
                 "author_urn": receipt.author_urn,
                 "approved_at": now, "status": "pending_publication"},
                session=session,
            )
            transitioned = await db.workflows.update_one(
                {"_id": workflow["_id"], "state": "AWAITING_APPROVAL",
                 "revision": workflow["revision"]},
                {"$set": {"state": "PUBLISH_PENDING",
                          "pending_preview.consumed": True,
                          "approval_id": receipt_id, "updated_at": now},
                 "$inc": {"revision": 1}}, session=session,
            )
            if transitioned.modified_count != 1:
                raise ApprovalIntakeError("Workflow changed during approval")
            await db.jobs.insert_one(
                {"_id": f"publish:{receipt_id}", "kind": "publish",
                 "dedupe_key": f"publish:{receipt_id}",
                 "workflow_id": workflow["_id"], "approval_id": receipt_id,
                 "status": "pending", "created_at": now}, session=session,
            )
            changed = await db.messages.update_one(
                {"_id": current["_id"], "status": "accepted_unprocessed"},
                {"$set": {"status": "approval_accepted", "processed_at": now}},
                session=session,
            )
            if changed.modified_count != 1:
                raise ApprovalIntakeError("Approval input changed during processing")
            return "approval_accepted"


async def drain_owner_messages(db, installation_id: str, publishing_enabled: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    while (result := await process_next_owner_message(db, installation_id, publishing_enabled)) is not None:
        counts[result] = counts.get(result, 0) + 1
    return counts
