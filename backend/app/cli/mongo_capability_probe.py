"""Check real MongoDB write guarantees in a uniquely named temporary database."""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import certifi
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from app.config import get_settings
from app.domain.approval import PublicationEnvelope
from app.integrations.telegram_pairing import PairingError, mark_receiver_restart, record_owner_update
from app.integrations.linkedin_posts import PostResult
from app.services.publication import publish_one


async def _check_durable_ingress(db) -> None:
    """Exercise the real ledger/cursor transaction without contacting Telegram."""
    now = datetime.now(timezone.utc)
    await db.owner_settings.insert_one(
        {"_id": "owner", "installation_id": "phase0", "next_event_seq": 0}
    )
    await db.connections.insert_one(
        {"_id": "telegram", "installation_id": "phase0", "status": "connected",
         "bot_id": "99", "sender_id": "42", "chat_id": "42", "binding_revision": 1}
    )
    await db.telegram_receivers.insert_one(
        {"_id": "99", "installation_id": "phase0", "lease_token": "phase0-lease",
         "lease_until": now + timedelta(minutes=5), "connection_epoch": 1,
         "next_offset": 1, "approval_barrier": False}
    )
    update = {"update_id": 1, "message": {
        "message_id": 1, "from": {"id": 42, "is_bot": False},
        "chat": {"id": 42, "type": "private"}, "text": "synthetic phase0 input",
    }}
    if await record_owner_update(db, "phase0", "99", "phase0-lease", update) != "owner_input":
        raise RuntimeError("Synthetic owner update was not accepted")
    saved = await db.messages.find_one({"_id": "telegram:99:1"})
    receiver = await db.telegram_receivers.find_one({"_id": "99"})
    if not saved or saved.get("ingress_seq") != 1 or receiver.get("next_offset") != 2:
        raise RuntimeError("Inbox, sequence, and cursor did not commit together")
    try:
        await record_owner_update(db, "phase0", "99", "phase0-lease", update)
    except DuplicateKeyError:
        pass
    else:
        raise RuntimeError("Duplicate provider update was accepted twice")
    await db.owner_settings.delete_one({"_id": "owner"})
    failed_update = {**update, "update_id": 2}
    try:
        await record_owner_update(db, "phase0", "99", "phase0-lease", failed_update)
    except PairingError:
        pass
    else:
        raise RuntimeError("Unbound owner update was accepted")
    if (await db.telegram_updates.find_one({"_id": "99:2"})
            or await db.messages.find_one({"_id": "telegram:99:2"})
            or (await db.telegram_receivers.find_one({"_id": "99"}))["next_offset"] != 2):
        raise RuntimeError("Failed transaction advanced the saved update cursor")
    await db.workflows.insert_one(
        {"_id": "preview", "active": True, "state": "AWAITING_APPROVAL",
         "revision": 1, "pending_preview": {"status": "active"}}
    )
    await mark_receiver_restart(db, "phase0", "99")
    preview = await db.workflows.find_one({"_id": "preview"})
    receiver = await db.telegram_receivers.find_one({"_id": "99"})
    if (preview.get("state") != "AWAITING_REVIEW"
            or preview["pending_preview"].get("status") != "invalidated_by_gap"
            or receiver.get("connection_epoch") != 2
            or receiver.get("approval_barrier") is not True):
        raise RuntimeError("Receiver restart did not invalidate old approval")


async def _check_unknown_publication(db) -> None:
    """Cross a real MongoDB send boundary with a fake, uncertain provider."""
    now = datetime.now(timezone.utc)
    envelope = PublicationEnvelope(
        draft_id="synthetic-draft", draft_version=1, text="Synthetic probe only",
        author_urn="urn:li:person:synthetic-member",
    )
    await db.messages.update_many({"direction": "inbound"}, {"$set": {"status": "unhandled"}})
    await db.owner_settings.insert_one(
        {"_id": "owner", "installation_id": "phase0", "next_event_seq": 7}
    )
    await db.connections.insert_one(
        {"_id": "linkedin", "installation_id": "phase0", "status": "connected",
         "member_id": "synthetic-member", "secret_ref": "synthetic-secret",
         "binding_revision": 1, "expires_at": now + timedelta(days=1)}
    )
    await db.telegram_receivers.update_one(
        {"_id": "99"}, {"$set": {"approval_barrier": False}}
    )
    await db.workflows.update_one(
        {"_id": "preview"},
        {"$set": {"state": "PUBLISH_PENDING", "revision": 2,
                  "approval_id": "synthetic-approval", "envelope_hash": envelope.digest(),
                  "linkedin_member_id": "synthetic-member",
                  "linkedin_connection_revision": 1,
                  "pending_preview": {"receiver_epoch": 2, "binding_revision": 1,
                                      "bot_id": "99", "chat_id": "42"}}},
    )
    await db.approval_receipts.insert_one(
        {"_id": "synthetic-approval", "status": "pending_publication",
         "envelope": envelope.model_dump(mode="json"),
         "envelope_hash": envelope.digest(), "approval_ingress_seq": 7}
    )
    await db.jobs.insert_one(
        {"_id": "synthetic-job", "kind": "publish", "status": "pending",
         "workflow_id": "preview", "approval_id": "synthetic-approval",
         "created_at": now}
    )
    calls = []

    async def uncertain_sender(*_args):
        calls.append(1)
        return PostResult("unknown", 503, None, "synthetic_uncertain")

    with TemporaryDirectory() as directory, patch(
        "app.services.publication.keyring.get_password", return_value="synthetic-token"
    ):
        settings = SimpleNamespace(
            publishing_enabled=True, linkedin_api_version="202609",
            app_data_dir=Path(directory),
        )
        first = await publish_one(
            db, settings, "phase0", "99", "phase0-lease",
            post_sender=uncertain_sender,
        )
        second = await publish_one(
            db, settings, "phase0", "99", "phase0-lease",
            post_sender=uncertain_sender,
        )
    attempt = await db.publication_attempts.find_one({"_id": "attempt:synthetic-approval"})
    workflow = await db.workflows.find_one({"_id": "preview"})
    if (first != "unknown" or second is not None or len(calls) != 1
            or attempt.get("status") != "unknown"
            or workflow.get("state") != "PUBLISH_UNKNOWN"):
        raise RuntimeError("Uncertain publication was retried or saved incorrectly")


async def inspect() -> None:
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required")
    probe_database = "linkedin_agent_phase0_" + secrets.token_hex(8)
    client = AsyncMongoClient(
        settings.mongodb_uri, serverSelectionTimeoutMS=8000,
        tlsCAFile=certifi.where(), retryWrites=False, w="majority",
    )
    db = client[probe_database]
    collection = db.get_collection(
        "capabilities", write_concern=WriteConcern("majority")
    )
    try:
        await collection.create_index("unique_key", unique=True)
        await collection.insert_one({"_id": "committed", "unique_key": "one"})
        duplicate_rejected = False
        try:
            await collection.insert_one({"_id": "duplicate", "unique_key": "one"})
        except DuplicateKeyError:
            duplicate_rejected = True
        if not duplicate_rejected:
            raise RuntimeError("Unique index did not reject a duplicate")
        async with client.start_session() as session:
            async with await session.start_transaction():
                await collection.insert_one(
                    {"_id": "rolled-back", "unique_key": "two"}, session=session
                )
                await session.abort_transaction()
        if await collection.find_one({"_id": "rolled-back"}):
            raise RuntimeError("Aborted transaction retained a document")
        if not await collection.find_one({"_id": "committed"}):
            raise RuntimeError("Majority-acknowledged write was not visible")
        await _check_durable_ingress(db)
        await _check_unknown_publication(db)
        print("MongoDB capability probe passed: majority write, unique index, rollback, durable ingress, duplicate rejection, restart barrier, one-shot uncertain publication")
    finally:
        try:
            for name in (
                "capabilities", "owner_settings", "connections", "telegram_receivers",
                "telegram_updates", "messages", "workflows", "jobs",
                "approval_receipts", "publication_attempts",
            ):
                await db.drop_collection(name)
        finally:
            await client.close()


def run() -> None:
    try:
        asyncio.run(inspect())
    except (PyMongoError, RuntimeError) as exc:
        raise SystemExit(f"MongoDB capability probe failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    run()
