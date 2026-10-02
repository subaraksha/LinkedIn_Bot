"""Durable, one-owner Telegram pairing prototype for the local CLI."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import certifi
from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.domain.telegram import OwnerBinding, authorized_text


class PairingError(RuntimeError):
    pass


def challenge_digest(challenge: str) -> str:
    return hashlib.sha256(challenge.encode()).hexdigest()


def pairing_candidate(update: dict, expected_digest: str) -> dict | None:
    if "edited_message" in update or not isinstance(update.get("message"), dict):
        return None
    message = update["message"]
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if (
        chat.get("type") != "private"
        or sender.get("is_bot") is not False
        or not isinstance(sender.get("id"), int)
        or not isinstance(chat.get("id"), int)
        or not isinstance(message.get("message_id"), int)
        or "forward_origin" in message
        or "forward_date" in message
        or not isinstance(message.get("text"), str)
    ):
        return None
    parts = message["text"].split(" ", 1)
    if len(parts) != 2 or parts[0] != "/start":
        return None
    if not secrets.compare_digest(challenge_digest(parts[1]), expected_digest):
        return None
    return {
        "sender_id": str(sender["id"]),
        "chat_id": str(chat["id"]),
        "first_name": sender.get("first_name"),
        "username": sender.get("username"),
        "message_id": str(message["message_id"]),
    }


def mongo_client(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(
        uri, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where(), w="majority"
    )


async def begin_pairing(db, installation_id: str, bot_id: str, challenge: str,
                        session_digest: str | None = None) -> None:
    owner = await db.owner_settings.find_one({"_id": "owner"})
    if not owner or owner.get("installation_id") != installation_id:
        raise PairingError("Owner database binding is missing or belongs to another installation")
    current = await db.connections.find_one({"_id": "telegram"})
    if current and current.get("status") == "connected":
        raise PairingError("Telegram is already paired; use an explicit re-pair flow")
    if current and current.get("bot_id") != bot_id:
        raise PairingError("Configured Telegram bot differs from the recorded bot")
    now = datetime.now(timezone.utc)
    pending = await db.telegram_pairings.find_one({"_id": bot_id})
    if (pending and pending.get("status") in {"waiting", "candidate"}
            and pending.get("expires_at")
            and pending["expires_at"].replace(tzinfo=timezone.utc) > now
            and pending.get("session_digest") != session_digest):
        raise PairingError("Another pairing session is already in progress")
    await db.telegram_pairings.replace_one(
        {"_id": bot_id},
        {
            "_id": bot_id,
            "installation_id": installation_id,
            "session_digest": session_digest,
            "challenge_hash": challenge_digest(challenge),
            "status": "waiting",
            "candidate": None,
            "expires_at": now + timedelta(minutes=10),
            "revision": (current or {}).get("binding_revision", 0),
            "created_at": now,
        },
        upsert=True,
    )
    await db.connections.update_one(
        {"_id": "telegram"},
        {"$set": {"installation_id": installation_id, "bot_id": bot_id, "status": "unpaired"}},
        upsert=True,
    )


async def claim_receiver(
    db, installation_id: str, bot_id: str, lease_token: str, lease_seconds: int = 30
) -> int | None:
    now = datetime.now(timezone.utc)
    try:
        record = await db.telegram_receivers.find_one_and_update(
            {"_id": bot_id, "$or": [
                {"lease_until": {"$lt": now}},
                {"lease_until": {"$exists": False}},
                {"lease_token": lease_token},
            ]},
            {"$set": {"installation_id": installation_id,
                      "lease_token": lease_token,
                      "lease_until": now + timedelta(seconds=lease_seconds)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise PairingError("Another Telegram receiver is already active for this bot") from exc
    if record.get("installation_id") != installation_id:
        raise PairingError("Telegram receiver belongs to another installation")
    return record.get("next_offset")


async def record_update(db, installation_id: str, bot_id: str, lease_token: str, update: dict) -> str:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        raise PairingError("Telegram supplied an update without a numeric ID")
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            pairing = await db.telegram_pairings.find_one({"_id": bot_id}, session=session)
            if not pairing or pairing.get("installation_id") != installation_id:
                raise PairingError("Pairing is no longer bound to this installation")
            now = datetime.now(timezone.utc)
            expiry = pairing["expires_at"].replace(tzinfo=timezone.utc)
            candidate = None
            if pairing["status"] == "waiting" and now < expiry:
                candidate = pairing_candidate(update, pairing["challenge_hash"])
            disposition = "pairing_candidate" if candidate else "rejected"
            await db.telegram_updates.insert_one(
                {"_id": f"{bot_id}:{update_id}", "bot_id": bot_id,
                 "update_id": update_id, "disposition": disposition, "received_at": now},
                session=session,
            )
            if candidate:
                accepted = await db.telegram_pairings.update_one(
                    {"_id": bot_id, "status": "waiting", "candidate": None},
                    {"$set": {"candidate": candidate, "status": "candidate"}},
                    session=session,
                )
                if accepted.modified_count != 1:
                    raise PairingError("Pairing candidate changed before update commit")
            result = await db.telegram_receivers.update_one(
                {"_id": bot_id, "lease_token": lease_token,
                 "lease_until": {"$gt": now}},
                {"$max": {"next_offset": update_id + 1},
                 "$set": {"installation_id": installation_id, "last_saved_at": now}},
                session=session,
            )
            if result.matched_count != 1:
                raise PairingError("Telegram receiver lease was lost before update commit")
            return disposition


async def release_receiver(db, bot_id: str, lease_token: str) -> None:
    await db.telegram_receivers.update_one(
        {"_id": bot_id, "lease_token": lease_token},
        {"$set": {"lease_until": datetime.now(timezone.utc)}},
    )


async def mark_receiver_restart(db, installation_id: str, bot_id: str) -> None:
    """A restart is a known polling gap, even if Telegram has no pending updates."""
    now = datetime.now(timezone.utc)
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            invalidated = await db.workflows.find(
                {"active": True, "state": {"$in": ["AWAITING_APPROVAL", "PUBLISH_PENDING"]}},
                session=session,
            ).to_list(length=100)
            result = await db.telegram_receivers.update_one(
                {"_id": bot_id, "installation_id": installation_id},
                {"$inc": {"connection_epoch": 1},
                 "$set": {"gap_since": now, "approval_barrier": True}},
                session=session,
            )
            if result.matched_count != 1:
                raise PairingError("Telegram receiver identity changed before restart")
            await db.workflows.update_many(
                {"active": True, "state": {"$in": ["AWAITING_APPROVAL", "PUBLISH_PENDING"]}},
                {"$set": {"state": "AWAITING_REVIEW",
                          "pending_preview.status": "invalidated_by_gap",
                          "updated_at": now}, "$inc": {"revision": 1}},
                session=session,
            )
            await db.jobs.update_many(
                {"kind": "publish", "status": "pending"},
                {"$set": {"status": "blocked_by_gap", "updated_at": now}},
                session=session,
            )
            for workflow in invalidated:
                pending = workflow.get("pending_preview") or {}
                if not pending.get("chat_id") or not pending.get("binding_revision"):
                    continue
                notice_id = f"publication-restart:{workflow['_id']}:{workflow['revision']}"
                await db.messages.insert_one({
                    "_id": notice_id, "channel": "telegram", "direction": "outbound",
                    "outbound_key": f"publication-notice:restart:{notice_id}",
                    "bot_id": bot_id, "chat_id": pending["chat_id"],
                    "binding_revision": pending["binding_revision"],
                    "text": "The bot restarted before LinkedIn publishing began. Nothing was posted. "
                            "The previous approval expired; reply FINAL for a fresh preview and command.",
                    "status": "pending", "created_at": now,
                }, session=session)


async def record_owner_update(db, installation_id: str, bot_id: str, lease_token: str, update: dict) -> str:
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        raise PairingError("Telegram supplied an update without a numeric ID")
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            connection = await db.connections.find_one({"_id": "telegram"}, session=session)
            if (not connection or connection.get("installation_id") != installation_id
                    or connection.get("bot_id") != bot_id or connection.get("status") != "connected"):
                raise PairingError("Telegram owner binding is not connected")
            binding = OwnerBinding(
                bot_id=bot_id,
                sender_id=connection["sender_id"],
                chat_id=connection["chat_id"],
                revision=connection["binding_revision"],
            )
            message = authorized_text(update, binding)
            if message and message.text.startswith("/start"):
                message = None  # Pairing payloads never enter conversation storage.
            now = datetime.now(timezone.utc)
            receiver = await db.telegram_receivers.find_one(
                {"_id": bot_id, "installation_id": installation_id,
                 "lease_token": lease_token, "lease_until": {"$gt": now}},
                session=session,
            )
            if not receiver:
                raise PairingError("Telegram receiver lease was lost before update commit")
            disposition = "owner_input" if message else "rejected"
            await db.telegram_updates.insert_one(
                {"_id": f"{bot_id}:{update_id}", "bot_id": bot_id,
                 "update_id": update_id, "disposition": disposition,
                 "binding_revision": binding.revision, "received_at": now},
                session=session,
            )
            if message:
                sequence = await db.owner_settings.find_one_and_update(
                    {"_id": "owner", "installation_id": installation_id},
                    {"$inc": {"next_event_seq": 1}},
                    return_document=ReturnDocument.AFTER, session=session,
                )
                if not sequence:
                    raise PairingError("Owner sequence binding changed before update commit")
                await db.messages.insert_one(
                    {"_id": f"telegram:{bot_id}:{update_id}",
                     "channel": "telegram", "direction": "inbound",
                     "bot_id": bot_id, "sender_id": message.sender_id,
                     "chat_id": message.chat_id, "provider_event_id": str(update_id),
                     "provider_message_id": message.message_id,
                     "binding_revision": binding.revision,
                     "receiver_epoch": receiver.get("connection_epoch", 0),
                     "text": message.text, "ingress_seq": sequence["next_event_seq"],
                     "status": "accepted_unprocessed",
                     "received_at": now},
                    session=session,
                )
            result = await db.telegram_receivers.update_one(
                {"_id": bot_id, "installation_id": installation_id,
                 "lease_token": lease_token, "lease_until": {"$gt": now}},
                {"$max": {"next_offset": update_id + 1},
                 "$set": {"last_saved_at": now}},
                session=session,
            )
            if result.matched_count != 1:
                raise PairingError("Telegram receiver lease was lost before update commit")
            return disposition


async def pending_candidate(db, installation_id: str, bot_id: str) -> dict | None:
    pairing = await db.telegram_pairings.find_one({"_id": bot_id})
    if not pairing or pairing.get("installation_id") != installation_id:
        return None
    if pairing.get("status") != "candidate":
        return None
    if pairing["expires_at"].replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        return None
    return pairing["candidate"]


async def confirm_candidate(db, installation_id: str, bot_id: str, sender_id: str,
                            session_digest: str | None = None) -> dict:
    candidate = await pending_candidate(db, installation_id, bot_id)
    if not candidate or candidate["sender_id"] != sender_id:
        raise PairingError("No matching unexpired candidate to confirm")
    now = datetime.now(timezone.utc)
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            filter_ = {"_id": bot_id, "installation_id": installation_id,
                       "status": "candidate", "candidate.sender_id": sender_id,
                       "expires_at": {"$gt": now}}
            if session_digest is not None:
                filter_["session_digest"] = session_digest
            result = await db.telegram_pairings.update_one(
                filter_,
                {"$set": {"status": "confirmed", "confirmed_at": now},
                 "$unset": {"challenge_hash": ""}},
                session=session,
            )
            if result.modified_count != 1:
                raise PairingError("Pairing expired or changed before confirmation")
            connection = await db.connections.update_one(
                {"_id": "telegram", "installation_id": installation_id, "bot_id": bot_id},
                {"$set": {"status": "connected", "sender_id": candidate["sender_id"],
                          "chat_id": candidate["chat_id"], "connected_at": now},
                 "$inc": {"binding_revision": 1}},
                session=session,
            )
            if connection.matched_count != 1:
                raise PairingError("Telegram connection changed before confirmation")
    return candidate
