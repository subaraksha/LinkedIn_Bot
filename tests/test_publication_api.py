"""Recovery controls require the local owner session and CSRF token."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.security import COOKIE, sessions
from app.main import app


class PublicationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="https://127.0.0.1:8765")
        session = sessions.new_session()
        self.client.cookies.set(COOKIE, session.id)
        self.csrf = session.csrf

    def tearDown(self):
        self.client.close()

    def test_recovery_routes_require_owner_session_and_csrf(self):
        unauthenticated = TestClient(app, base_url="https://127.0.0.1:8765")
        self.assertEqual(unauthenticated.get("/api/v1/publications/workflow/recovery").status_code, 401)
        self.assertEqual(unauthenticated.post("/api/v1/publications/workflow/quiesce",
            headers={"Origin": "https://127.0.0.1:8765"}).status_code, 401)
        unauthenticated.close()
        self.assertEqual(self.client.post("/api/v1/publications/workflow/quiesce",
            headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/publications/workflow/resolve", json={
            "attempt_id": "attempt:approval", "expected_workflow_revision": 1,
            "idempotency_key": "request-123", "outcome": "not_published",
            "acknowledgement": True}, headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)

    def test_resolution_rejects_invalid_body_before_database(self):
        with patch("app.api.publications.context", side_effect=AssertionError("database must not be reached")):
            response = self.client.post("/api/v1/publications/workflow/resolve", json={
                "attempt_id": "short", "expected_workflow_revision": 0,
                "idempotency_key": "x", "outcome": "published",
                "acknowledgement": True},
                headers={"Origin": "https://127.0.0.1:8765",
                         "X-CSRF-Token": self.csrf})
            self.assertEqual(response.status_code, 422)
