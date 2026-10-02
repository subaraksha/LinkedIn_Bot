"""Stage one exact owner-reviewed post for the phase 0 live publication path."""

import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from app.domain.approval import PublicationEnvelope
from app.domain.preview import PendingPreview
from app.services.preview_delivery import PreviewDeliveryError


def canonical_post_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if (not normalized.strip() or len(normalized) > 3000
            or "\x00" in normalized or any(ord(char) < 32 and char not in "\n\t" for char in normalized)):
        raise PreviewDeliveryError("Post body must contain 1–3000 characters of supported text")
    return normalized


async def stage_final_preview(
    db, installation_id: str, bot_id: str, post_text: str
) -> tuple[str, str]:
    """Create immutable draft and outbox, with challenge inactive until every send succeeds."""
    body = canonical_post_text(post_text)
    now = datetime.now(timezone.utc)
    await db.workflows.create_index(
        [("active", 1)], unique=True, partialFilterExpression={"active": True}
    )
    await db.workflows.create_index("short_code", unique=True)
    owner = await db.owner_settings.find_one({"_id": "owner"})
    telegram = await db.connections.find_one({"_id": "telegram"})
    linkedin = await db.connections.find_one({"_id": "linkedin"})
    receiver = await db.telegram_receivers.find_one({"_id": bot_id})
    if not owner or owner.get("installation_id") != installation_id:
        raise PreviewDeliveryError("Owner database binding is unavailable")
    if (not telegram or telegram.get("installation_id") != installation_id
            or telegram.get("bot_id") != bot_id or telegram.get("status") != "connected"):
        raise PreviewDeliveryError("Telegram owner binding is unavailable")
    if (not linkedin or linkedin.get("installation_id") != installation_id
            or linkedin.get("status") != "connected"):
        raise PreviewDeliveryError("LinkedIn connection is unavailable")
    if (not receiver or receiver.get("installation_id") != installation_id
            or not receiver.get("lease_until")
            or receiver["lease_until"].replace(tzinfo=timezone.utc) <= now):
        raise PreviewDeliveryError("Telegram receiver must be running before final preview")
    if await db.workflows.find_one({"active": True}):
        raise PreviewDeliveryError("An active workflow already exists")
    workflow_id = str(uuid.uuid4())
    draft_id = str(uuid.uuid4())
    code = "W" + secrets.token_hex(4).upper()
    challenge = secrets.token_hex(8).upper()
    envelope = PublicationEnvelope(
        draft_id=draft_id, draft_version=1, text=body,
        author_urn=f"urn:li:person:{linkedin['member_id']}",
    )
    pending = PendingPreview(
        workflow_code=code, challenge_code=challenge,
        envelope_hash=envelope.digest(), draft_version=1,
        author_urn=envelope.author_urn, bot_id=bot_id,
        sender_id=telegram["sender_id"], chat_id=telegram["chat_id"],
        binding_revision=telegram["binding_revision"],
        receiver_epoch=receiver.get("connection_epoch", 0),
        expires_at=now + timedelta(hours=24),
        body_delivery_confirmed=False, control_delivery_confirmed=False,
    )
    intro = (
        f"FINAL PREVIEW — {code} V1\n"
        f"LinkedIn account: {linkedin.get('member_name') or 'connected member'}\n"
        "Audience: Public — anyone on LinkedIn; resharing allowed.\n"
        "The next message is the exact post body."
    )
    control = (
        f"To publish that exact body now, send this as a new message:\n"
        f"PUBLISH {code} V1 {challenge}\n"
        "To change it, do not send the command."
    )
    if any(len(part) > 4096 for part in (intro, body, control)):
        raise PreviewDeliveryError("Preview exceeds Telegram message size")
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            await db.workflows.insert_one(
                {"_id": workflow_id, "short_code": code, "kind": "phase0_live_post",
                 "active": True, "state": "PREVIEW_SENDING", "revision": 1,
                 "draft_id": draft_id, "envelope_hash": envelope.digest(),
                 "linkedin_member_id": linkedin["member_id"],
                 "linkedin_connection_revision": linkedin.get("binding_revision", 0),
                 "preview_issued_after_seq": owner.get("next_event_seq", 0),
                 "pending_preview": {**asdict(pending), "status": "inactive"},
                 "created_at": now, "updated_at": now},
                session=session,
            )
            await db.draft_versions.insert_one(
                {"_id": draft_id, "workflow_id": workflow_id, "version": 1,
                 "body": body, "envelope": envelope.model_dump(mode="json"),
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
                for index, text in enumerate((intro, body, control))
            ], session=session)
    return workflow_id, code
