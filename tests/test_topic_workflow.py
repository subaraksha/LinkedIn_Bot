"""Phase 3 schedule, research boundary and saved conversation checks."""

import os
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.services.topic_research import ResearchError, canonical_url, load_sources
from app.services.topic_workflow import (
    TopicWorkflowError, current_workflow, dispatch_topic_messages, format_shortlist,
    get_schedule, handle_topic_message, next_occurrence, save_schedule, start_workflow,
    run_research_job, schedule_tick, parse_owner_topic,
)
from app.storage.foundation import ensure_foundation


class TopicRulesTests(unittest.TestCase):
    def test_owner_topic_accepts_no_space_and_preserves_short_idea_for_feedback(self):
        self.assertEqual(parse_owner_topic("MY TOPIC:Jev"), "Jev")
        self.assertEqual(parse_owner_topic("my topic: Java development"), "Java development")
        self.assertIsNone(parse_owner_topic("TOPICS"))

    def test_sources_are_editable_and_canonical_links_stay_on_domain(self):
        self.assertGreaterEqual(len(load_sources()["feeds"]), 1)
        self.assertEqual(canonical_url("https://blog.python.org/post?a=1#fragment", "blog.python.org"),
                         "https://blog.python.org/post?a=1")
        for link in ("http://blog.python.org/post", "https://127.0.0.1/private",
                     "https://blog.python.org.evil.invalid/post", "file:///etc/passwd"):
            with self.assertRaises(ResearchError):
                canonical_url(link, "blog.python.org")

    def test_schedule_handles_timezone_and_dst_gap(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(0, "09:00", "Asia/Kolkata", now),
                         datetime(2026, 10, 5, 3, 30, tzinfo=timezone.utc))
        spring = next_occurrence(6, "02:30", "America/New_York",
                                 datetime(2026, 3, 8, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(spring.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).hour, 3)
        with self.assertRaises(TopicWorkflowError):
            next_occurrence(8, "25:00", "Asia/Kolkata", now)

    def test_telegram_shortlist_is_bounded_and_revision_explicit(self):
        topic = {"title": "A" * 160, "why_now": "B" * 500, "why_you": "C" * 500,
                 "angle": "D" * 500, "sources": [{"url": "https://blog.python.org/" + "x" * 600}]}
        text = format_shortlist({"shortlist_revision": 2, "shortlist": [topic] * 5})
        self.assertLess(len(text), 3900)
        self.assertIn("CHOOSE 2:<number>", text)
        first = format_shortlist({"shortlist_revision": 1, "shortlist": [topic] * 5})
        self.assertIn("CHOOSE <number>", first)


@unittest.skipUnless(os.getenv("RUN_MONGO_INTEGRATION") == "1", "requires disposable MongoDB test")
class TopicWorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_research_job_and_downtime_create_one_current_cycle(self):
        client = mongo_client(get_settings().mongodb_uri)
        name = "topic_test_" + secrets.token_hex(8)
        db = client[name]
        try:
            await ensure_foundation(db, "synthetic-installation")
            await save_schedule(db, "synthetic-installation", expected_revision=0,
                weekday=0, local_time="09:00", timezone_name="Asia/Kolkata", enabled=True)
            await db.weekly_schedules.update_one({"_id": "owner"}, {"$set": {
                "next_at": datetime.now(timezone.utc) - timedelta(weeks=3)}})
            await schedule_tick(db, "synthetic-installation")
            workflow = await db.workflows.find_one({"active": True})
            self.assertEqual(workflow["state"], "RESEARCH_PENDING")
            self.assertEqual(await db.jobs.count_documents({"kind": "research_topics"}), 1)
            await schedule_tick(db, "synthetic-installation")
            self.assertEqual(await db.jobs.count_documents({"kind": "research_topics"}), 1)
            topics = [{"title": f"Synthetic topic {number}", "why_now": "A synthetic update",
                "why_you": "Matches synthetic interests", "angle": "Discuss a tradeoff",
                "source_ids": [f"snapshot-{number}"], "sources": [{"id": f"snapshot-{number}",
                    "url": "https://blog.python.org/", "title": "Synthetic source"}]}
                for number in range(4)]
            async def synthetic_research(*_):
                # A TOPICS reply while research runs changes the conversation revision.
                # It must not discard the completed shortlist.
                await db.workflows.update_one({"_id": workflow["_id"]},
                    {"$inc": {"revision": 1}})
                return topics
            with patch("app.services.topic_workflow.research_topics", synthetic_research):
                self.assertTrue(await run_research_job(db, "synthetic-installation", "synthetic-key",
                                                       "synthetic-model", "worker-1"))
            updated = await db.workflows.find_one({"_id": workflow["_id"]})
            self.assertEqual(updated["state"], "AWAITING_TOPIC")
            self.assertEqual(len(updated["shortlist"]), 4)
            self.assertEqual(await db.research_runs.count_documents({"status": "succeeded"}), 1)
            self.assertEqual(await db.messages.count_documents({"kind": "phase3_topic",
                "status": "pending"}), 1)
        finally:
            await client.drop_database(name)
            await client.close()

    async def test_schedule_selection_input_skip_and_outbox(self):
        client = mongo_client(get_settings().mongodb_uri)
        name = "topic_test_" + secrets.token_hex(8)
        db = client[name]
        try:
            await ensure_foundation(db, "synthetic-installation")
            initial = await get_schedule(db, "synthetic-installation", "Asia/Kolkata")
            self.assertFalse(initial["enabled"])
            saved = await save_schedule(db, "synthetic-installation", expected_revision=0,
                weekday=0, local_time="09:00", timezone_name="Asia/Kolkata", enabled=True)
            self.assertEqual(saved["revision"], 1)
            with self.assertRaises(TopicWorkflowError):
                await save_schedule(db, "synthetic-installation", expected_revision=0,
                    weekday=1, local_time="10:00", timezone_name="Asia/Kolkata", enabled=True)
            workflow = await start_workflow(db, "synthetic-installation", slot_key="synthetic",
                                            origin="manual")
            with self.assertRaises(TopicWorkflowError):
                await start_workflow(db, "synthetic-installation", slot_key="duplicate", origin="manual")
            topic = {"title": "Synthetic database release", "why_now": "A synthetic release",
                "why_you": "Relevant to databases", "angle": "Discuss a design tradeoff",
                "source_ids": ["synthetic-snapshot"],
                "sources": [{"id": "synthetic-snapshot", "url": "https://blog.python.org/",
                             "title": "Synthetic source"}]}
            await db.workflows.update_one({"_id": workflow["_id"]}, {"$set": {
                "state": "AWAITING_TOPIC", "shortlist_revision": 1, "shortlist": [topic]},
                "$inc": {"revision": 1}})

            async def owner(text, number):
                message = {"_id": f"synthetic:{number}", "channel": "telegram", "direction": "inbound",
                    "text": text, "status": "accepted_unprocessed", "ingress_seq": number,
                    "received_at": datetime.now(timezone.utc)}
                await db.messages.insert_one(message)
                result = await handle_topic_message(db, message, "synthetic-installation")
                self.assertEqual(result, "topic_processed")
                self.assertEqual((await db.messages.find_one({"_id": message["_id"]}))["status"], "processed")

            await owner("CHOOSE 0:1", 1)
            self.assertEqual((await current_workflow(db, "synthetic-installation"))["state"], "AWAITING_TOPIC")
            await owner("CHOOSE 1", 2)
            self.assertEqual((await current_workflow(db, "synthetic-installation"))["state"], "AWAITING_INPUT")
            await owner("I am curious about the design tradeoff.", 3)
            await owner("EXPLORING", 4)
            current = await current_workflow(db, "synthetic-installation")
            self.assertEqual(current["state"], "READY_FOR_DRAFT")
            self.assertEqual(current["experience"], "exploring")
            self.assertIn("curious", current["perspective"])
            await db.connections.insert_one({"_id": "telegram", "installation_id": "synthetic-installation",
                "status": "connected", "chat_id": "synthetic-chat"})

            class FakeTelegram:
                def __init__(self): self.sent = []
                async def send_text(self, chat_id, text):
                    self.sent.append((chat_id, text))
                    return str(len(self.sent))

            telegram = FakeTelegram()
            await dispatch_topic_messages(db, telegram, "synthetic-installation")
            self.assertEqual(len(telegram.sent), 3)
            await dispatch_topic_messages(db, telegram, "synthetic-installation")
            self.assertEqual(len(telegram.sent), 3)
            await owner("SKIP WEEK", 5)
            self.assertIsNone(await current_workflow(db, "synthetic-installation"))
        finally:
            await client.drop_database(name)
            await client.close()
