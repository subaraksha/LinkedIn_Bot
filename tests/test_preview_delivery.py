import unittest
from types import SimpleNamespace

from app.integrations.telegram import TelegramError
from app.services.preview_delivery import PreviewDeliveryError, dispatch_demo_preview


class Cursor:
    def __init__(self, parts):
        self.parts = parts

    def sort(self, *_args):
        return self

    async def to_list(self, length):
        return [dict(part) for part in self.parts[:length]]


class Messages:
    def __init__(self):
        self.parts = [
            {"_id": str(index), "workflow_id": "demo", "direction": "outbound",
             "part_index": index, "bot_id": "bot", "chat_id": "chat",
             "binding_revision": 1, "linkedin_member_id": "member",
             "text": f"part {index}", "status": "pending"}
            for index in range(3)
        ]

    def find(self, _query):
        return Cursor(self.parts)

    async def update_one(self, query, change):
        part = next(item for item in self.parts if item["_id"] == query["_id"])
        if part["status"] != query["status"]:
            return SimpleNamespace(modified_count=0)
        part.update(change["$set"])
        return SimpleNamespace(modified_count=1)


class Connection:
    def __init__(self, data):
        self.data = data

    async def find_one(self, _query):
        return self.data


class FakeDb:
    def __init__(self):
        self.messages = Messages()
        self.connections = Connection({
            "status": "connected", "bot_id": "bot", "chat_id": "chat",
            "binding_revision": 1, "member_id": "member",
        })
        self.workflows = SimpleNamespace(update_one=self._update_workflow)

    async def _update_workflow(self, *_args):
        raise AssertionError("A failed preview must not be marked delivered")


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_text(self, _chat_id, text):
        self.sent.append(text)
        if len(self.sent) == 2:
            raise TelegramError("ambiguous send")
        return "provider-1"


class PreviewDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_sends_unicode_links_and_punctuation_without_mutation(self):
        db = FakeDb()
        body = "Learning queues 🙂\n\nWhy does order matter? #backend https://example.com/a?x=1&y=2"
        db.messages.parts[1]["text"] = body
        states = []

        async def complete(_query, change):
            states.append(change["$set"]["state"])
            return SimpleNamespace(modified_count=1)

        db.workflows = SimpleNamespace(update_one=complete)

        class AcceptingTelegram:
            def __init__(self):
                self.sent = []

            async def send_text(self, _chat_id, text):
                self.sent.append(text)
                return str(len(self.sent))

        telegram = AcceptingTelegram()
        await dispatch_demo_preview(db, "demo", telegram)
        self.assertEqual(telegram.sent[1], body)
        self.assertEqual([part["status"] for part in db.messages.parts], ["accepted"] * 3)
        self.assertEqual(states, ["PREVIEW_DEMO_DELIVERED"])

    async def test_unknown_body_stops_before_control_and_never_retries(self):
        db = FakeDb()
        telegram = FakeTelegram()
        with self.assertRaises(PreviewDeliveryError):
            await dispatch_demo_preview(db, "demo", telegram)
        self.assertEqual([part["status"] for part in db.messages.parts],
                         ["accepted", "delivery_unknown", "pending"])
        self.assertEqual(len(telegram.sent), 2)
        with self.assertRaises(PreviewDeliveryError):
            await dispatch_demo_preview(db, "demo", telegram)
        self.assertEqual(len(telegram.sent), 2)
