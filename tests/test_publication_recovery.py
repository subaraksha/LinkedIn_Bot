"""Uncertain publication resolution has no path to a second send."""

import copy
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services.publication_recovery import (
    RecoveryError, recovery_status, resolve_outcome, validated_post_url,
    quiesce_attempt,
)
from app.services.draft_workflow import _cancel_unsent_publication


def field(doc, path):
    for segment in path.split("."):
        doc = doc.get(segment) if isinstance(doc, dict) else None
    return doc


def matches(doc, query):
    for key, expected in query.items():
        actual = field(doc, key)
        if isinstance(expected, dict):
            if "$exists" in expected and (actual is not None) != expected["$exists"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Session:
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def start_transaction(self): return self


class Collection:
    def __init__(self, documents=()):
        self.documents = {doc["_id"]: copy.deepcopy(doc) for doc in documents}

    async def find_one(self, query, **_):
        return next((copy.deepcopy(doc) for doc in self.documents.values()
                     if matches(doc, query)), None)

    async def insert_one(self, doc, **_):
        if doc["_id"] in self.documents:
            raise AssertionError("duplicate insert")
        self.documents[doc["_id"]] = copy.deepcopy(doc)

    async def update_one(self, query, changes, **_):
        doc = next((doc for doc in self.documents.values() if matches(doc, query)), None)
        if doc is None:
            return SimpleNamespace(modified_count=0)
        self._change(doc, changes)
        return SimpleNamespace(modified_count=1)

    async def update_many(self, query, changes, **_):
        docs = [doc for doc in self.documents.values() if matches(doc, query)]
        for doc in docs:
            self._change(doc, changes)
        return SimpleNamespace(modified_count=len(docs))

    @staticmethod
    def _change(doc, changes):
        for path, value in changes.get("$set", {}).items():
            target = doc
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        for path in changes.get("$unset", {}):
            target = doc
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.get(part, {})
            target.pop(parts[-1], None)
        for path, value in changes.get("$inc", {}).items():
            doc[path] = doc.get(path, 0) + value


def fixture():
    db = SimpleNamespace(client=SimpleNamespace(start_session=Session))
    db.workflows = Collection([{"_id": "workflow", "installation_id": "install",
        "state": "PUBLISH_UNKNOWN", "revision": 7, "active": True,
        "pending_preview": {"bot_id": "bot", "chat_id": "chat", "binding_revision": 2}}])
    db.publication_attempts = Collection([{"_id": "attempt:approval", "workflow_id": "workflow",
        "approval_id": "approval", "status": "unknown", "send_started_at": datetime.now(timezone.utc)}])
    db.runtime_control = Collection([{"_id": "publication", "blocked": True,
        "attempt_id": "attempt:approval", "recovery_generation": "generation",
        "quiesced_at": datetime.now(timezone.utc)}])
    db.telegram_receivers = Collection()
    db.jobs = Collection([{"_id": "publish:approval", "kind": "publish",
        "approval_id": "approval", "status": "blocked"}])
    db.approval_receipts = Collection([{"_id": "approval", "status": "unknown"}])
    db.publication_resolutions = Collection()
    db.messages = Collection()
    return db


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_change_cancels_unsent_job_and_approval(self):
        db = fixture()
        db.jobs.documents["publish:approval"]["status"] = "pending"
        db.jobs.documents["publish:approval"]["workflow_id"] = "workflow"
        db.approval_receipts.documents["approval"]["status"] = "pending_publication"
        await _cancel_unsent_publication(db, {"_id": "workflow",
            "state": "PUBLISH_PENDING", "approval_id": "approval"}, None)
        self.assertEqual(db.jobs.documents["publish:approval"]["status"], "cancelled_by_owner")
        self.assertEqual(db.approval_receipts.documents["approval"]["status"], "cancelled_by_owner")

    async def test_quiescence_checks_exact_process_and_worker_lock(self):
        db = fixture()
        attempt = db.publication_attempts.documents["attempt:approval"]
        attempt.update({"worker_pid": 12345, "worker_created_at": 42.5})
        db.runtime_control.documents["publication"].pop("quiesced_at")
        db.workflows.documents["workflow"]["pending_preview"]["bot_id"] = "bot"
        db.telegram_receivers.documents["bot"] = {"_id": "bot", "installation_id": "install",
            "worker_pid": 23456, "worker_created_at": 50.5}
        processes = {pid: MagicMock() for pid in (12345, 23456)}
        processes[12345].create_time.return_value = 42.5
        processes[23456].create_time.return_value = 50.5
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication_recovery.psutil.Process", side_effect=lambda pid: processes[pid]
        ), patch("app.services.publication_recovery.WorkerLock") as lock:
            result = await quiesce_attempt(db, Path(directory), "attempt:approval")
        self.assertTrue(result["quiesced"])
        for process in processes.values():
            process.terminate.assert_called_once()
            process.wait.assert_called_once()
        lock.assert_called_once()
        self.assertIn("quiesced_at", db.runtime_control.documents["publication"])

    def test_only_canonical_linkedin_post_urls(self):
        self.assertEqual(validated_post_url("https://www.linkedin.com/feed/update/urn:li:share:123"),
                         ("https://www.linkedin.com/feed/update/urn:li:share:123", "urn:li:share:123"))
        for url in ("http://www.linkedin.com/feed/update/urn:li:share:123",
                    "https://linkedin.com.evil.test/feed/update/urn:li:share:123",
                    "https://www.linkedin.com/in/person"):
            with self.assertRaises(RecoveryError):
                validated_post_url(url)

    async def test_not_published_requires_quiescence_and_creates_no_job(self):
        db = fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = await resolve_outcome(db, Path(directory), "install",
                workflow_id="workflow", attempt_id="attempt:approval",
                expected_revision=7, idempotency_key="request-123",
                outcome="not_published", acknowledgement=True, post_url=None)
            self.assertEqual(result["outcome"], "not_published")
            self.assertEqual(db.workflows.documents["workflow"]["state"], "AWAITING_REVIEW")
            self.assertEqual(db.jobs.documents["publish:approval"]["status"], "resolved_not_published")
            self.assertEqual(len(db.jobs.documents), 1)
            self.assertEqual(await resolve_outcome(db, Path(directory), "install",
                workflow_id="workflow", attempt_id="attempt:approval",
                expected_revision=7, idempotency_key="request-123",
                outcome="not_published", acknowledgement=True, post_url=None), result)
        self.assertFalse(db.runtime_control.documents["publication"]["blocked"])

    async def test_unquiesced_attempt_stays_unresolved(self):
        db = fixture()
        db.runtime_control.documents["publication"].pop("quiesced_at")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RecoveryError):
                await resolve_outcome(db, Path(directory), "install",
                    workflow_id="workflow", attempt_id="attempt:approval",
                    expected_revision=7, idempotency_key="request-456",
                    outcome="not_published", acknowledgement=True, post_url=None)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISH_UNKNOWN")
        self.assertFalse(db.publication_resolutions.documents)

    async def test_reported_post_url_is_labeled_owner_reported(self):
        db = fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = await resolve_outcome(db, Path(directory), "install",
                workflow_id="workflow", attempt_id="attempt:approval",
                expected_revision=7, idempotency_key="request-789",
                outcome="published", acknowledgement=True,
                post_url="https://www.linkedin.com/feed/update/urn:li:share:123")
        self.assertEqual(result["evidence_source"], "owner_reported")
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISHED")
        self.assertFalse(db.workflows.documents["workflow"]["active"])
        with self.assertRaises(RecoveryError):
            await recovery_status(db, "workflow")
