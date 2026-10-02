"""Repeatable Phase 1 database foundation for one bound installation."""

import hashlib
import json
from datetime import datetime, timezone

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError


class FoundationError(RuntimeError):
    pass


INDEXES = (
    ("sources", [("kind", ASCENDING), ("content_hash", ASCENDING)], "unique_source_snapshot", True, {"content_hash": {"$exists": True}}),
    ("research_items", [("canonical_url", ASCENDING)], "unique_research_url", True, {"canonical_url": {"$exists": True}}),
    ("research_snapshots", [("research_item_id", ASCENDING), ("fetched_at", ASCENDING), ("_id", ASCENDING)], "research_snapshot_history", False, None),
    ("knowledge_entries", [("status", ASCENDING), ("publication_permission", ASCENDING), ("type", ASCENDING)], "knowledge_context", False, None),
    ("workflows", [("active", ASCENDING)], "active_1", True, {"active": True}),
    ("workflows", [("short_code", ASCENDING)], "short_code_1", True, None),
    ("draft_versions", [("workflow_id", ASCENDING), ("version", ASCENDING)], "unique_draft_version", True, None),
    ("draft_versions", [("generation_id", ASCENDING)], "unique_draft_generation", True, {"generation_id": {"$exists": True}}),
    ("messages", [("outbound_key", ASCENDING)], "unique_outbound_key", True, {"outbound_key": {"$exists": True}}),
    ("messages", [("channel", ASCENDING), ("bot_id", ASCENDING), ("provider_event_id", ASCENDING)], "unique_inbound_event", True, {"direction": "inbound", "provider_event_id": {"$exists": True}}),
    ("messages", [("workflow_id", ASCENDING), ("ingress_seq", ASCENDING)], "workflow_ingress_order", False, None),
    ("messages", [("direction", ASCENDING), ("status", ASCENDING), ("available_at", ASCENDING)], "outbound_dispatch", False, None),
    ("approval_receipts", [("challenge_id", ASCENDING)], "unique_approval_challenge", True, {"challenge_id": {"$exists": True}}),
    ("approval_receipts", [("source_message_id", ASCENDING)], "unique_approval_message", True, {"source_message_id": {"$exists": True}}),
    ("publication_attempts", [("approval_id", ASCENDING)], "unique_publication_approval", True, {"approval_id": {"$exists": True}}),
    ("publication_resolutions", [("attempt_id", ASCENDING)], "unique_publication_resolution", True, {"attempt_id": {"$exists": True}}),
    ("publication_resolutions", [("idempotency_key", ASCENDING)], "unique_resolution_request", True, {"idempotency_key": {"$exists": True}}),
    ("telegram_updates", [("bot_id", ASCENDING), ("update_id", ASCENDING)], "unique_telegram_update", True, None),
    ("telegram_pairings", [("challenge_hash", ASCENDING)], "unique_pairing_challenge", True, {"challenge_hash": {"$exists": True}}),
    ("jobs", [("dedupe_key", ASCENDING)], "unique_job_dedupe", True, {"dedupe_key": {"$exists": True}}),
    ("jobs", [("status", ASCENDING), ("available_at", ASCENDING), ("lease_until", ASCENDING)], "claimable_jobs", False, None),
)


async def ensure_foundation(db, installation_id: str) -> None:
    """Bind the database, then install indexes; existing owner fields are preserved."""
    now = datetime.now(timezone.utc)
    checksum = hashlib.sha256(json.dumps(INDEXES, sort_keys=True).encode()).hexdigest()
    migration = await db.schema_migrations.find_one({"_id": "phase1_foundation_v1"})
    if migration and migration.get("checksum") not in (None, checksum):
        raise FoundationError("Foundation definition changed; a new migration is required")
    existing_owner = await db.owner_settings.find_one({"_id": "owner"})
    if existing_owner is None:
        for provider in ("linkedin", "telegram"):
            connection = await db.connections.find_one({"_id": provider})
            if connection and connection.get("installation_id") != installation_id:
                raise FoundationError("Configured database belongs to another installation")
    try:
        await db.owner_settings.update_one(
            {"_id": "owner"},
            {"$setOnInsert": {
                "installation_id": installation_id, "schema_version": 1,
                "revision": 1, "next_event_seq": 0,
                "knowledge_revision": 0, "preferences_revision": 0,
                "created_at": now, "updated_at": now,
            }},
            upsert=True,
        )
    except DuplicateKeyError as exc:
        raise FoundationError("Owner binding changed during setup") from exc
    owner = await db.owner_settings.find_one({"_id": "owner"})
    if not owner or owner.get("installation_id") != installation_id:
        raise FoundationError("Configured database belongs to another installation")
    defaults = {
        "schema_version": 1, "revision": 1, "next_event_seq": 0,
        "knowledge_revision": 0, "preferences_revision": 0,
        "created_at": now, "updated_at": now,
    }
    for field, value in defaults.items():
        if field not in owner:
            await db.owner_settings.update_one(
                {"_id": "owner", "installation_id": installation_id,
                 field: {"$exists": False}},
                {"$set": {field: value}},
            )
    for collection, keys, name, unique, partial in INDEXES:
        options = {"name": name, "unique": unique}
        if partial is not None:
            options["partialFilterExpression"] = partial
        try:
            await db[collection].create_index(keys, **options)
        except DuplicateKeyError as exc:
            raise FoundationError(f"Existing duplicate records prevent index {name}") from exc
    await db.schema_migrations.update_one(
        {"_id": "phase1_foundation_v1"},
        {"$setOnInsert": {"applied_at": now, "installation_id": installation_id},
         "$set": {"checksum": checksum}},
        upsert=True,
    )
