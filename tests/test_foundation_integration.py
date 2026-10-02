"""Opt-in checks against a disposable database on the configured MongoDB cluster."""

import os
import secrets
import unittest
from datetime import timedelta

from app.config import get_settings
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
        finally:
            await client.drop_database(name)
            await client.close()
