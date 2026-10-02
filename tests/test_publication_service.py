import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.approval import PublicationEnvelope
from app.integrations.linkedin_posts import PostResult
from app.services.publication import PublicationError, publish_one, recover_inflight
from app.storage.publication_journal import save_success


def field_value(document, path):
    value = document
    for segment in path.split("."):
        value = value.get(segment) if isinstance(value, dict) else None
    return value


def matches(document, query):
    for key, expected in query.items():
        actual = field_value(document, key)
        if isinstance(expected, dict):
            if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                return False
        elif actual != expected:
            return False
    return True


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def start_transaction(self):
        return self


class Collection:
    def __init__(self, documents=()):
        self.documents = {item["_id"]: copy.deepcopy(item) for item in documents}

    async def find_one(self, query, **_kwargs):
        return next((copy.deepcopy(doc) for doc in self.documents.values() if matches(doc, query)), None)

    def find(self, query):
        documents = [copy.deepcopy(doc) for doc in self.documents.values() if matches(doc, query)]
        return SimpleNamespace(to_list=lambda length: _list_first(documents, length))

    async def insert_one(self, document, **_kwargs):
        if document["_id"] in self.documents:
            raise AssertionError("Duplicate insert")
        self.documents[document["_id"]] = copy.deepcopy(document)

    async def update_one(self, query, change, **_kwargs):
        item = next((doc for doc in self.documents.values() if matches(doc, query)), None)
        if item is None:
            return SimpleNamespace(modified_count=0)
        for path, value in change.get("$set", {}).items():
            target = item
            parts = path.split(".")
            for segment in parts[:-1]:
                target = target.setdefault(segment, {})
            target[parts[-1]] = value
        for path, value in change.get("$inc", {}).items():
            item[path] = item.get(path, 0) + value
        return SimpleNamespace(modified_count=1)


async def _list_first(documents, length):
    return documents[:length]


def fixture():
    now = datetime.now(timezone.utc)
    envelope = PublicationEnvelope(
        draft_id="draft", draft_version=1, text="An approved test post",
        author_urn="urn:li:person:member",
    )
    db = SimpleNamespace(client=SimpleNamespace(start_session=Session))
    db.jobs = Collection([{
        "_id": "publish:approval:workflow", "kind": "publish", "status": "pending",
        "workflow_id": "workflow", "approval_id": "approval:workflow", "created_at": now,
    }])
    db.workflows = Collection([{
        "_id": "workflow", "state": "PUBLISH_PENDING", "active": True,
        "revision": 2, "approval_id": "approval:workflow",
        "envelope_hash": envelope.digest(), "linkedin_member_id": "member",
        "pending_preview": {"receiver_epoch": 3, "binding_revision": 1,
                            "bot_id": "bot", "chat_id": "chat"},
    }])
    db.approval_receipts = Collection([{
        "_id": "approval:workflow", "status": "pending_publication",
        "envelope": envelope.model_dump(mode="json"), "envelope_hash": envelope.digest(),
        "approval_ingress_seq": 7,
    }])
    db.connections = Collection([
        {"_id": "telegram", "installation_id": "install", "status": "connected",
         "bot_id": "bot", "chat_id": "chat", "binding_revision": 1},
        {"_id": "linkedin", "installation_id": "install", "status": "connected",
         "member_id": "member", "secret_ref": "secret", "expires_at": now + timedelta(days=1)},
    ])
    db.telegram_receivers = Collection([{
        "_id": "bot", "installation_id": "install", "lease_token": "lease",
        "lease_until": now + timedelta(minutes=1), "approval_barrier": False,
        "connection_epoch": 3,
    }])
    db.messages = Collection()
    db.owner_settings = Collection([{
        "_id": "owner", "installation_id": "install", "next_event_seq": 7,
    }])
    db.runtime_control = Collection()
    db.publication_attempts = Collection()
    return db


class PublicationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_after_send_boundary_marks_unknown_without_resend(self):
        db = fixture()
        db.jobs.documents["publish:approval:workflow"]["status"] = "running"
        db.workflows.documents["workflow"]["state"] = "PUBLISHING"
        db.publication_attempts.documents["attempt:approval:workflow"] = {
            "_id": "attempt:approval:workflow", "approval_id": "approval:workflow",
            "status": "PUBLISHING",
        }
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(await recover_inflight(db, Path(directory)), 1)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISH_UNKNOWN")
        self.assertEqual(db.publication_attempts.documents["attempt:approval:workflow"]["status"], "unknown")
        self.assertEqual(db.jobs.documents["publish:approval:workflow"]["status"], "blocked")

    async def test_restart_uses_confirmed_journal_without_resend(self):
        db = fixture()
        db.jobs.documents["publish:approval:workflow"]["status"] = "running"
        db.workflows.documents["workflow"]["state"] = "PUBLISHING"
        db.publication_attempts.documents["attempt:approval:workflow"] = {
            "_id": "attempt:approval:workflow", "approval_id": "approval:workflow",
            "status": "PUBLISHING",
        }
        with tempfile.TemporaryDirectory() as directory:
            save_success(Path(directory), "attempt:approval:workflow", "urn:li:share:123")
            self.assertEqual(await recover_inflight(db, Path(directory)), 1)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISHED")
        self.assertEqual(db.publication_attempts.documents["attempt:approval:workflow"]["status"], "confirmed")

    async def test_one_confirmed_send_creates_one_attempt_and_no_retry(self):
        db = fixture()
        calls = []

        async def fake_sender(token, version, envelope):
            calls.append((token, version, envelope.digest()))
            return PostResult("confirmed", 201, "urn:li:share:123", "created")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication.keyring.get_password", return_value="fake-token"
        ):
            settings = SimpleNamespace(publishing_enabled=True, linkedin_api_version="202609",
                                       app_data_dir=Path(directory))
            self.assertEqual(await publish_one(db, settings, "install", "bot", "lease",
                                               post_sender=fake_sender), "confirmed")
            self.assertIsNone(await publish_one(db, settings, "install", "bot", "lease",
                                                 post_sender=fake_sender))
        self.assertEqual(len(calls), 1)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISHED")
        self.assertEqual(db.publication_attempts.documents["attempt:approval:workflow"]["status"], "confirmed")
        self.assertEqual(len(db.messages.documents), 1)

    async def test_unknown_outcome_never_retries(self):
        db = fixture()
        calls = []

        async def fake_sender(*_args):
            calls.append(1)
            return PostResult("unknown", 503, None, "unexpected_response")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication.keyring.get_password", return_value="fake-token"
        ):
            settings = SimpleNamespace(publishing_enabled=True, linkedin_api_version="202609",
                                       app_data_dir=Path(directory))
            self.assertEqual(await publish_one(db, settings, "install", "bot", "lease",
                                               post_sender=fake_sender), "unknown")
            self.assertIsNone(await publish_one(db, settings, "install", "bot", "lease",
                                                 post_sender=fake_sender))
        self.assertEqual(len(calls), 1)
        self.assertEqual(db.workflows.documents["workflow"]["state"], "PUBLISH_UNKNOWN")

    async def test_restart_barrier_prevents_send(self):
        db = fixture()
        db.telegram_receivers.documents["bot"]["approval_barrier"] = True
        calls = []

        async def fake_sender(*_args):
            calls.append(1)
            return PostResult("confirmed", 201, "urn:li:share:123", "created")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication.keyring.get_password", return_value="fake-token"
        ):
            settings = SimpleNamespace(publishing_enabled=True, linkedin_api_version="202609",
                                       app_data_dir=Path(directory))
            with self.assertRaises(PublicationError):
                await publish_one(db, settings, "install", "bot", "lease",
                                  post_sender=fake_sender)
        self.assertFalse(calls)
        self.assertFalse(db.publication_attempts.documents)

    async def test_reconnected_linkedin_binding_prevents_old_approval_send(self):
        db = fixture()
        db.workflows.documents["workflow"]["linkedin_connection_revision"] = 1
        db.connections.documents["linkedin"]["binding_revision"] = 2
        calls = []

        async def fake_sender(*_args):
            calls.append(1)
            return PostResult("confirmed", 201, "urn:li:share:123", "created")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication.keyring.get_password", return_value="fake-token"
        ):
            settings = SimpleNamespace(publishing_enabled=True, linkedin_api_version="202609",
                                       app_data_dir=Path(directory))
            with self.assertRaises(PublicationError):
                await publish_one(db, settings, "install", "bot", "lease",
                                  post_sender=fake_sender)
        self.assertFalse(calls)

    async def test_later_owner_input_blocks_send_at_transaction_boundary(self):
        db = fixture()
        db.owner_settings.documents["owner"]["next_event_seq"] = 8
        calls = []

        async def fake_sender(*_args):
            calls.append(1)
            return PostResult("confirmed", 201, "urn:li:share:123", "created")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.publication.keyring.get_password", return_value="fake-token"
        ):
            settings = SimpleNamespace(publishing_enabled=True, linkedin_api_version="202609",
                                       app_data_dir=Path(directory))
            with self.assertRaises(PublicationError):
                await publish_one(db, settings, "install", "bot", "lease",
                                  post_sender=fake_sender)
        self.assertFalse(calls)
