"""Persist a dry-run preview before dispatching exact Telegram messages."""

import uuid
from datetime import datetime, timezone

from app.domain.approval import PublicationEnvelope
from app.integrations.telegram import TelegramClient, TelegramError


class PreviewDeliveryError(RuntimeError):
    pass


DEMO_BODY = (
    "Testing the LinkedIn Post Agent preview.\n\n"
    "This is a dry run. Nothing in this message will be published to LinkedIn."
)


async def create_demo_preview(db, installation_id: str, bot_id: str) -> str:
    """Create one fixture draft and ordered outbox. No approval challenge exists."""
    owner = await db.owner_settings.find_one({"_id": "owner"})
    telegram = await db.connections.find_one({"_id": "telegram"})
    linkedin = await db.connections.find_one({"_id": "linkedin"})
    if not owner or owner.get("installation_id") != installation_id:
        raise PreviewDeliveryError("Owner database binding is unavailable")
    if (not telegram or telegram.get("installation_id") != installation_id
            or telegram.get("bot_id") != bot_id or telegram.get("status") != "connected"):
        raise PreviewDeliveryError("Telegram is not paired to this installation")
    if (not linkedin or linkedin.get("installation_id") != installation_id
            or linkedin.get("status") != "connected"):
        raise PreviewDeliveryError("LinkedIn is not connected to this installation")
    workflow_id = str(uuid.uuid4())
    draft_id = str(uuid.uuid4())
    code = "DEMO" + workflow_id.split("-")[0].upper()
    envelope = PublicationEnvelope(
        draft_id=draft_id, draft_version=1, text=DEMO_BODY,
        author_urn=f"urn:li:person:{linkedin['member_id']}",
    )
    intro = (
        f"DRY RUN PREVIEW — {code} V1\n"
        f"Destination: {linkedin.get('member_name') or 'connected LinkedIn account'}\n"
        "The next message contains the exact draft body."
    )
    control = (
        "Dry-run preview delivered. Publishing is disabled. "
        "No approval command is active; replies to this preview will not publish a post."
    )
    parts = (intro, DEMO_BODY, control)
    if any(len(part) > 4096 for part in parts):
        raise PreviewDeliveryError("Preview exceeds Telegram message size")
    now = datetime.now(timezone.utc)
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            await db.workflows.insert_one(
                {"_id": workflow_id, "kind": "phase0_preview_demo", "short_code": code,
                 "state": "PREVIEW_DEMO", "active": False, "created_at": now,
                 "draft_id": draft_id, "envelope_hash": envelope.digest(),
                 "telegram_binding_revision": telegram["binding_revision"],
                 "linkedin_member_id": linkedin["member_id"],
                 "approval_challenge": None},
                session=session,
            )
            await db.draft_versions.insert_one(
                {"_id": draft_id, "workflow_id": workflow_id, "version": 1,
                 "body": DEMO_BODY, "envelope": envelope.model_dump(mode="json"),
                 "envelope_hash": envelope.digest(), "created_at": now},
                session=session,
            )
            await db.messages.insert_many([
                {"_id": f"preview:{workflow_id}:{index}",
                 "channel": "telegram", "direction": "outbound",
                 "outbound_key": f"preview:{workflow_id}:{index}",
                 "workflow_id": workflow_id, "part_index": index,
                 "bot_id": bot_id, "chat_id": telegram["chat_id"],
                 "binding_revision": telegram["binding_revision"],
                 "linkedin_member_id": linkedin["member_id"],
                 "text": text, "status": "pending", "created_at": now}
                for index, text in enumerate(parts)
            ], session=session)
    return workflow_id


async def dispatch_demo_preview(db, workflow_id: str, telegram_client: TelegramClient) -> None:
    await dispatch_preview(db, workflow_id, telegram_client, demo=True)


async def dispatch_preview(
    db, workflow_id: str, telegram_client: TelegramClient, *, demo: bool
) -> None:
    parts = await db.messages.find(
        {"workflow_id": workflow_id, "direction": "outbound"}
    ).sort("part_index", 1).to_list(length=3)
    if len(parts) != 3 or [part["part_index"] for part in parts] != [0, 1, 2]:
        raise PreviewDeliveryError("Preview outbox is incomplete")
    for part in parts:
        if part["status"] != "pending":
            raise PreviewDeliveryError("Preview part is not safely pending; no automatic resend")
        connection = await db.connections.find_one({"_id": "telegram"})
        linkedin = await db.connections.find_one({"_id": "linkedin"})
        if (not connection or connection.get("status") != "connected"
                or connection.get("bot_id") != part["bot_id"]
                or connection.get("chat_id") != part["chat_id"]
                or connection.get("binding_revision") != part["binding_revision"]
                or not linkedin or linkedin.get("member_id") != part["linkedin_member_id"]):
            raise PreviewDeliveryError("Connection changed before preview delivery")
        claimed = await db.messages.update_one(
            {"_id": part["_id"], "status": "pending"},
            {"$set": {"status": "sending", "attempt_started_at": datetime.now(timezone.utc)}},
        )
        if claimed.modified_count != 1:
            raise PreviewDeliveryError("Preview part was claimed elsewhere")
        try:
            provider_message_id = await telegram_client.send_text(part["chat_id"], part["text"])
        except TelegramError as exc:
            await db.messages.update_one(
                {"_id": part["_id"], "status": "sending"},
                {"$set": {"status": "delivery_unknown"}},
            )
            raise PreviewDeliveryError("Telegram delivery is unconfirmed; no retry was made") from exc
        stored = await db.messages.update_one(
            {"_id": part["_id"], "status": "sending"},
            {"$set": {"status": "accepted", "provider_message_id": provider_message_id,
                      "accepted_at": datetime.now(timezone.utc)}},
        )
        if stored.modified_count != 1:
            raise PreviewDeliveryError("Telegram accepted a part but local receipt is unresolved")
    if demo:
        result = await db.workflows.update_one(
            {"_id": workflow_id, "state": "PREVIEW_DEMO"},
            {"$set": {"state": "PREVIEW_DEMO_DELIVERED",
                      "delivered_at": datetime.now(timezone.utc)}},
        )
    else:
        workflow = await db.workflows.find_one({"_id": workflow_id})
        pending = workflow.get("pending_preview") if workflow else None
        if not pending:
            raise PreviewDeliveryError("Pending approval challenge is missing")
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                earlier_input = await db.messages.find_one(
                    {"channel": "telegram", "direction": "inbound",
                     "ingress_seq": {"$gt": workflow.get("preview_issued_after_seq", 0)}},
                    session=session,
                )
                receiver = await db.telegram_receivers.find_one(
                    {"_id": pending["bot_id"],
                     "connection_epoch": pending["receiver_epoch"]}, session=session,
                )
                if earlier_input or not receiver:
                    raise PreviewDeliveryError("Owner messages or receiver epoch changed during preview")
                result = await db.workflows.update_one(
                    {"_id": workflow_id, "state": "PREVIEW_SENDING",
                     "pending_preview.status": "inactive"},
                    {"$set": {"state": "AWAITING_APPROVAL",
                              "pending_preview.status": "active",
                              "pending_preview.body_delivery_confirmed": True,
                              "pending_preview.control_delivery_confirmed": True,
                              "delivered_at": datetime.now(timezone.utc)}},
                    session=session,
                )
                if result.modified_count != 1:
                    raise PreviewDeliveryError("Preview was delivered but activation state is unresolved")
                await db.telegram_receivers.update_one(
                    {"_id": pending["bot_id"],
                     "connection_epoch": pending["receiver_epoch"]},
                    {"$set": {"approval_barrier": False}}, session=session,
                )
    if result.modified_count != 1:
        raise PreviewDeliveryError("Preview was delivered but activation state is unresolved")
