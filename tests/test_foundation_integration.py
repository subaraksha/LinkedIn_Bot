"""Opt-in checks against a disposable database on the configured MongoDB cluster."""

import os
import secrets
import unittest
from datetime import timedelta

from app.config import get_settings
from app.integrations.telegram_pairing import (
    begin_pairing, claim_receiver, confirm_candidate, pending_candidate,
    record_update, release_receiver,
)
from app.integrations.telegram_pairing import mongo_client
from app.storage.foundation import FoundationError, ensure_foundation
from app.storage.jobs import claim_job, enqueue_job, finish_job, utcnow


@unittest.skipUnless(os.getenv("RUN_MONGO_INTEGRATION") == "1", "requires disposable MongoDB test")
class FoundationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_indexes_and_job_recovery(self) -> None:
        uri = get_settings().mongodb_uri
        self.assertTrue(uri, "MONGODB_URI must be configured")
        client = mongo_client(uri)
        name = "linkedin_agent_phase1_" + secrets.token_hex(8)
        db = client[name]
        try:
            await ensure_foundation(db, "test-installation")
            await ensure_foundation(db, "test-installation")
            with self.assertRaises(FoundationError):
                await ensure_foundation(db, "different-installation")

            first = await enqueue_job(db, kind="synthetic", dedupe_key="one", payload={"value": 1})
            repeated = await enqueue_job(db, kind="synthetic", dedupe_key="one", payload={"value": 1})
            self.assertEqual(first["_id"], repeated["_id"])
            with self.assertRaises(ValueError):
                await enqueue_job(db, kind="synthetic", dedupe_key="one", payload={"value": 2})

            claimed = await claim_job(db, kinds=["synthetic"], worker_id="worker-a")
            self.assertIsNotNone(claimed)
            self.assertIsNone(await claim_job(db, kinds=["synthetic"], worker_id="worker-b"))
            self.assertFalse(await finish_job(
                db, job_id=claimed["_id"], worker_id="worker-b",
                revision=claimed["revision"], status="succeeded",
            ))
            self.assertTrue(await finish_job(
                db, job_id=claimed["_id"], worker_id="worker-a",
                revision=claimed["revision"], status="succeeded",
            ))

            await enqueue_job(
                db, kind="synthetic", dedupe_key="reclaim", payload={},
                available_at=utcnow() - timedelta(seconds=1),
            )
            stale = await claim_job(db, kinds=["synthetic"], worker_id="worker-a")
            await db.jobs.update_one(
                {"_id": stale["_id"]},
                {"$set": {"lease_until": utcnow() - timedelta(seconds=1)}},
            )
            reclaimed = await claim_job(db, kinds=["synthetic"], worker_id="worker-b")
            self.assertEqual(stale["_id"], reclaimed["_id"])
            self.assertGreater(reclaimed["revision"], stale["revision"])
            self.assertFalse(await finish_job(
                db, job_id=stale["_id"], worker_id="worker-a",
                revision=stale["revision"], status="succeeded",
            ))

            await begin_pairing(db, "test-installation", "999", "sample-challenge",
                                session_digest="synthetic-session-digest")
            lease = "test-lease"
            await claim_receiver(db, "test-installation", "999", lease)
            update = {"update_id": 1, "message": {
                "message_id": 4, "text": "/start sample-challenge",
                "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                "chat": {"id": 42, "type": "private"},
            }}
            self.assertEqual(await record_update(
                db, "test-installation", "999", lease, update
            ), "pairing_candidate")
            pairing = await db.telegram_pairings.find_one({"_id": "999"})
            self.assertEqual(pairing["session_digest"], "synthetic-session-digest")
            self.assertEqual((await pending_candidate(db, "test-installation", "999"))["sender_id"], "42")
            from app.integrations.telegram_pairing import PairingError
            with self.assertRaises(PairingError):
                await confirm_candidate(db, "test-installation", "999", "42",
                                        session_digest="wrong-session")
            candidate = await confirm_candidate(db, "test-installation", "999", "42",
                                                session_digest="synthetic-session-digest")
            self.assertEqual(candidate["sender_id"], "42")
            await release_receiver(db, "999", lease)
        finally:
            await client.drop_database(name)
            await client.close()
