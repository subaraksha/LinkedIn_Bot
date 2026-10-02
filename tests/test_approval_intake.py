import copy
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domain.approval import PublicationEnvelope
from app.domain.preview import PendingPreview
from app.services.approval_intake import process_next_owner_message


def matches(document, query):
    for key, expected in query.items():
        value = document
        for segment in key.split("."):
            value = value.get(segment) if isinstance(value, dict) else None
        if value != expected:
            return False
    return True


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def start_transaction(self):
        return self


class FakeCollection:
    def __init__(self, documents=()):
        self.documents = {item["_id"]: copy.deepcopy(item) for item in documents}

    async def find_one(self, query, **_kwargs):
        return next((copy.deepcopy(doc) for doc in self.documents.values() if matches(doc, query)), None)

    def find(self, query, **_kwargs):
        return FakeCursor([copy.deepcopy(doc) for doc in self.documents.values() if matches(doc, query)])

    async def insert_one(self, document, **_kwargs):
        if document["_id"] in self.documents:
            raise AssertionError("Duplicate insert")
        self.documents[document["_id"]] = copy.deepcopy(document)

    async def update_one(self, query, change, **_kwargs):
        doc = next((doc for doc in self.documents.values() if matches(doc, query)), None)
        if doc is None:
            return SimpleNamespace(modified_count=0)
        for key, value in change.get("$set", {}).items():
            target = doc
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        for key, value in change.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value
        return SimpleNamespace(modified_count=1)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, field, direction):
        self.documents.sort(key=lambda doc: doc[field], reverse=direction < 0)
        return self

    async def to_list(self, length):
        return self.documents[:length]


def fixture_db(receiver_epoch=3):
    envelope = PublicationEnvelope(
        draft_id="draft", draft_version=1, text="Exact post 🙂",
        author_urn="urn:li:person:member",
    )
    pending = PendingPreview(
        workflow_code="W1", challenge_code="CODE123", envelope_hash=envelope.digest(),
        draft_version=1, author_urn=envelope.author_urn,
        bot_id="bot", sender_id="owner", chat_id="chat", binding_revision=1,
        receiver_epoch=3, expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        body_delivery_confirmed=True, control_delivery_confirmed=True,
    )
    db = SimpleNamespace(client=SimpleNamespace(start_session=FakeSession))
    db.messages = FakeCollection([
        {"_id": "inbound", "channel": "telegram", "direction": "inbound",
         "status": "accepted_unprocessed", "text": "PUBLISH W1 V1 CODE123",
         "ingress_seq": 1, "received_at": datetime.now(timezone.utc),
         "provider_event_id": "8", "provider_message_id": "9",
         "sender_id": "owner", "chat_id": "chat", "bot_id": "bot",
         "binding_revision": 1, "receiver_epoch": receiver_epoch},
        {"_id": "intro", "workflow_id": "workflow", "direction": "outbound",
         "part_index": 0, "status": "accepted", "text": "Public destination"},
        {"_id": "body", "workflow_id": "workflow", "direction": "outbound",
         "part_index": 1, "status": "accepted", "text": envelope.text},
        {"_id": "control", "workflow_id": "workflow", "direction": "outbound",
         "part_index": 2, "status": "accepted",
         "text": "To publish: PUBLISH W1 V1 CODE123"},
    ])
    db.connections = FakeCollection([
        {"_id": "telegram", "installation_id": "install", "status": "connected",
         "bot_id": "bot", "sender_id": "owner", "chat_id": "chat", "binding_revision": 1},
        {"_id": "linkedin", "installation_id": "install", "status": "connected",
         "member_id": "member"},
    ])
    db.workflows = FakeCollection([{
        "_id": "workflow", "short_code": "W1", "active": True,
        "state": "AWAITING_APPROVAL", "revision": 1, "draft_id": "draft",
        "envelope_hash": envelope.digest(), "linkedin_member_id": "member",
        "pending_preview": {**asdict(pending), "status": "active"},
    }])
    db.draft_versions = FakeCollection([{
        "_id": "draft", "body": envelope.text,
        "envelope": envelope.model_dump(mode="json"),
        "envelope_hash": envelope.digest(),
    }])
    db.telegram_receivers = FakeCollection([{
        "_id": "bot", "connection_epoch": 3,
    }])
    db.approval_receipts = FakeCollection()
    db.jobs = FakeCollection()
    db.owner_settings = FakeCollection([{"_id": "owner", "installation_id": "install",
                                         "profile_revision": 1}])
    return db


class ApprovalIntakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase4_command_validates_without_publish_job(self):
        db = fixture_db()
        workflow = db.workflows.documents["workflow"]
        workflow.update({"kind": "weekly_topics", "installation_id": "install",
                         "preview_id": "preview-1", "preview_issued_after_seq": 0,
                         "linkedin_connection_revision": 0, "preview_profile_revision": 1})
        db.draft_versions.documents["draft"]["version"] = 1
        for part in db.messages.documents.values():
            if part.get("direction") == "outbound":
                part.update({"kind": "phase4_preview", "preview_id": "preview-1"})
        self.assertEqual(await process_next_owner_message(db, "install", False),
                         "phase4_approval_verified")
        self.assertEqual(workflow["state"], "APPROVAL_VERIFIED")
        self.assertEqual(next(iter(db.approval_receipts.documents.values()))["status"],
                         "validated_only")
        self.assertFalse(db.jobs.documents)

    async def test_fresh_live_preview_creates_one_publish_job(self):
        db = fixture_db()
        workflow = db.workflows.documents["workflow"]
        workflow.update({"kind": "weekly_topics", "installation_id": "install",
                         "preview_id": "preview-2", "preview_issued_after_seq": 0,
                         "linkedin_connection_revision": 0, "preview_profile_revision": 1})
        workflow["pending_preview"]["mode"] = "publish"
        db.draft_versions.documents["draft"]["version"] = 1
        for part in db.messages.documents.values():
            if part.get("direction") == "outbound":
                part.update({"kind": "phase4_preview", "preview_id": "preview-2"})
        self.assertEqual(await process_next_owner_message(db, "install", True),
                         "publication_approved")
        self.assertEqual(workflow["state"], "PUBLISH_PENDING")
        receipt = next(iter(db.approval_receipts.documents.values()))
        self.assertEqual(receipt["status"], "pending_publication")
        self.assertEqual(len(db.jobs.documents), 1)
        self.assertEqual(next(iter(db.jobs.documents.values()))["approval_id"], receipt["_id"])
        self.assertIsNone(await process_next_owner_message(db, "install", True))

    async def test_phase4_changed_body_rejects_approval(self):
        db = fixture_db()
        workflow = db.workflows.documents["workflow"]
        workflow.update({"kind": "weekly_topics", "installation_id": "install",
                         "preview_id": "preview-1", "preview_issued_after_seq": 0,
                         "linkedin_connection_revision": 0, "preview_profile_revision": 1})
        db.draft_versions.documents["draft"]["version"] = 1
        for part in db.messages.documents.values():
            if part.get("direction") == "outbound":
                part.update({"kind": "phase4_preview", "preview_id": "preview-1"})
        db.messages.documents["body"]["text"] = "Changed draft"
        self.assertEqual(await process_next_owner_message(db, "install", False),
                         "phase4_approval_rejected")
        self.assertFalse(db.approval_receipts.documents)
        self.assertFalse(db.jobs.documents)

    async def test_exact_command_creates_one_receipt_and_one_job(self):
        db = fixture_db()
        self.assertEqual(await process_next_owner_message(db, "install", True), "approval_accepted")
        self.assertIsNone(await process_next_owner_message(db, "install", True))
        self.assertEqual(len(db.approval_receipts.documents), 1)
        self.assertEqual(len(db.jobs.documents), 1)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISH_PENDING")

    async def test_stale_epoch_and_disabled_publishing_create_no_job(self):
        for db, enabled, reason in (
            (fixture_db(receiver_epoch=2), True, "stale_owner_message"),
            (fixture_db(), False, "publication_disabled"),
        ):
            self.assertEqual(await process_next_owner_message(db, "install", enabled), reason)
            self.assertFalse(db.approval_receipts.documents)
            self.assertFalse(db.jobs.documents)

    async def test_changed_preview_body_cannot_approve(self):
        db = fixture_db()
        db.messages.documents["body"]["text"] = "Different post"
        self.assertEqual(await process_next_owner_message(db, "install", True), "draft_changed")
        self.assertFalse(db.jobs.documents)
