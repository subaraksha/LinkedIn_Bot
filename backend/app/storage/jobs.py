"""Durable job primitives for later authoring and research work."""

import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue_job(db, *, kind: str, dedupe_key: str, payload: dict,
                      available_at: datetime | None = None) -> dict:
    """Create one logical job; retries return the already durable record."""
    now = utcnow()
    record = {
        "_id": str(uuid.uuid4()), "schema_version": 1,
        "kind": kind, "dedupe_key": dedupe_key, "payload": payload,
        "status": "pending", "available_at": available_at or now,
        "attempt_count": 0, "revision": 1,
        "created_at": now, "updated_at": now,
    }
    try:
        await db.jobs.insert_one(record)
        return record
    except DuplicateKeyError:
        existing = await db.jobs.find_one({"dedupe_key": dedupe_key})
        if existing is None:
            raise
        if existing.get("kind") != kind or existing.get("payload") != payload:
            raise ValueError("Job dedupe key already belongs to different work")
        return existing


async def claim_job(db, *, kinds: list[str], worker_id: str,
                    lease_seconds: int = 90) -> dict | None:
    """Atomically claim due work or reclaim a job whose lease expired."""
    if not kinds or lease_seconds <= 0:
        raise ValueError("Job kinds and positive lease duration are required")
    now = utcnow()
    return await db.jobs.find_one_and_update(
        {"kind": {"$in": kinds}, "available_at": {"$lte": now},
         "$or": [{"status": "pending"},
                 {"status": "running", "lease_until": {"$lte": now}}]},
        {"$set": {"status": "running", "lease_owner": worker_id,
                  "lease_until": now + timedelta(seconds=lease_seconds),
                  "updated_at": now},
         "$inc": {"attempt_count": 1, "revision": 1}},
        sort=[("available_at", 1), ("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def finish_job(db, *, job_id: str, worker_id: str, revision: int,
                     status: str, error_code: str | None = None) -> bool:
    """Only the current lease holder can complete the claimed revision."""
    if status not in {"succeeded", "failed"}:
        raise ValueError("Terminal job status must be succeeded or failed")
    now = utcnow()
    result = await db.jobs.update_one(
        {"_id": job_id, "status": "running", "lease_owner": worker_id,
         "lease_until": {"$gt": now}, "revision": revision},
        {"$set": {"status": status, "error_code": error_code,
                  "finished_at": now, "updated_at": now},
         "$unset": {"lease_owner": "", "lease_until": ""},
         "$inc": {"revision": 1}},
    )
    return result.modified_count == 1
