"""Pause and saved-progress behavior for the final owner workflow."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.security import COOKIE, sessions
from app.main import app

from app.services.topic_workflow import (
    TopicWorkflowError, continuation_recap, get_schedule, handle_continue_message,
    set_schedule_paused,
)


class ContinuationTests(unittest.TestCase):
    def test_draft_recap_is_explicit_about_saved_state_and_old_sources(self):
        now = datetime.now(timezone.utc)
        recap = continuation_recap({"state": "DRAFT_REVIEW",
            "selected_topic": {"title": "Practical AI"}, "draft_version": 3,
            "updated_at": now - timedelta(days=8)}, now)
        self.assertIn("Practical AI", recap)
        self.assertIn("Draft V3 is saved", recap)
        self.assertIn("time-sensitive claims", recap)
        self.assertIn("may need to be resent", recap)

    def test_approval_recap_requires_fresh_preview(self):
        recap = continuation_recap({"state": "AWAITING_APPROVAL",
            "selected_topic": {"title": "Practical AI"}, "draft_version": 2})
        self.assertIn("earlier approval is no longer usable", recap)
        self.assertIn("FINAL", recap)


class SchedulePauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_utc_invitation_is_explicit_for_browser_formatting(self):
        stored = datetime(2026, 10, 2, 17, 45)
        db = SimpleNamespace(weekly_schedules=SimpleNamespace(find_one=AsyncMock(
            return_value={"_id": "owner", "installation_id": "install", "enabled": True,
                "paused": False, "weekday": 4, "local_time": "23:15",
                "timezone": "Asia/Kolkata", "next_at": stored, "revision": 1})))
        schedule = await get_schedule(db, "install", "Asia/Kolkata")
        self.assertEqual(schedule["next_at"].isoformat(), "2026-10-02T17:45:00+00:00")

    async def test_pause_clears_next_invitation_and_resume_schedules_future(self):
        now = datetime.now(timezone.utc)
        active = {"enabled": True, "paused": False, "weekday": 0,
            "local_time": "09:00", "timezone": "Asia/Kolkata",
            "next_at": now + timedelta(days=1), "revision": 3}
        paused = {**active, "enabled": False, "paused": True,
                  "next_at": None, "revision": 4}
        resumed = {**active, "revision": 5}
        db = SimpleNamespace(weekly_schedules=SimpleNamespace(
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1))))
        with patch("app.services.topic_workflow.get_schedule", AsyncMock(side_effect=[active, paused, paused, resumed])):
            self.assertEqual((await set_schedule_paused(db, "install", "Asia/Kolkata",
                expected_revision=3, paused=True))["paused"], True)
            self.assertEqual((await set_schedule_paused(db, "install", "Asia/Kolkata",
                expected_revision=4, paused=False))["enabled"], True)
        first = db.weekly_schedules.update_one.await_args_list[0].args[1]["$set"]
        second = db.weekly_schedules.update_one.await_args_list[1].args[1]["$set"]
        self.assertIsNone(first["next_at"])
        self.assertGreater(second["next_at"], now)

    async def test_pause_requires_active_schedule(self):
        db = SimpleNamespace(weekly_schedules=SimpleNamespace(update_one=AsyncMock()))
        with patch("app.services.topic_workflow.get_schedule", AsyncMock(return_value={
            "enabled": False, "paused": False, "revision": 0})):
            with self.assertRaises(TopicWorkflowError):
                await set_schedule_paused(db, "install", "Asia/Kolkata",
                    expected_revision=0, paused=True)
        db.weekly_schedules.update_one.assert_not_awaited()


class ContinueSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_continue_without_active_conversation_queues_reply(self):
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False
            async def start_transaction(self): return self

        db = SimpleNamespace(
            client=SimpleNamespace(start_session=lambda: Session()),
            workflows=SimpleNamespace(find_one=AsyncMock(return_value=None)),
            messages=SimpleNamespace(update_one=AsyncMock(
                return_value=SimpleNamespace(modified_count=1)), insert_one=AsyncMock()),
        )
        result = await handle_continue_message(db, {"_id": "input", "text": "CONTINUE"},
                                               "install")
        self.assertEqual(result, "no_active_conversation")
        notice = db.messages.insert_one.await_args.args[0]
        self.assertTrue(notice["standalone_owner_notice"])
        self.assertIn("no active conversation", notice["text"])

    async def test_continue_cancels_unsent_approval_and_requests_fresh_preview(self):
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False
            async def start_transaction(self): return self

        workflow = {"_id": "workflow", "installation_id": "install",
            "kind": "weekly_topics", "active": True, "state": "PUBLISH_PENDING",
            "revision": 4, "draft_version": 2, "approval_id": "approval",
            "preview_id": "preview", "selected_topic": {"title": "Practical AI"}}
        message = {"_id": "message", "text": "CONTINUE", "status": "accepted_unprocessed"}
        db = SimpleNamespace(
            client=SimpleNamespace(start_session=lambda: Session()),
            workflows=SimpleNamespace(find_one=AsyncMock(return_value=workflow),
                update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1))),
            messages=SimpleNamespace(find_one=AsyncMock(return_value=message),
                update_one=AsyncMock(), update_many=AsyncMock()),
            jobs=SimpleNamespace(update_many=AsyncMock()),
            approval_receipts=SimpleNamespace(update_one=AsyncMock()),
        )
        with patch("app.services.topic_workflow.queue_message", AsyncMock()) as queued:
            self.assertEqual(await handle_continue_message(db, message, "install"), "continued")
        update = db.workflows.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["state"], "AWAITING_REVIEW")
        self.assertEqual(update["pending_preview.status"], "invalidated_by_owner")
        self.assertEqual(db.jobs.update_many.await_args.args[1]["$set"]["status"],
                         "cancelled_by_owner")
        self.assertIn("FINAL", queued.await_args.args[3])


class LifecycleApiTests(unittest.TestCase):
    def test_pause_requires_csrf_and_history_requires_owner_session(self):
        client = TestClient(app, base_url="https://127.0.0.1:8765")
        try:
            self.assertEqual(client.get("/api/v1/topics/history/workflow").status_code, 401)
            session = sessions.new_session()
            client.cookies.set(COOKIE, session.id)
            self.assertEqual(client.put("/api/v1/topics/schedule/pause",
                json={"expected_revision": 1, "paused": True},
                headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)
        finally:
            client.close()
