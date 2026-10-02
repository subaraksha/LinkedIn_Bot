"""Input validation and session protection for the professional intake API."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.security import COOKIE, sessions
from app.main import app


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, base_url="https://127.0.0.1:8765")
        session = sessions.new_session()
        self.client.cookies.set(COOKIE, session.id)
        self.csrf = session.csrf

    def tearDown(self):
        self.client.close()

    def test_requires_session_and_csrf(self):
        unauthenticated = TestClient(app, base_url="https://127.0.0.1:8765")
        self.assertEqual(unauthenticated.get("/api/v1/knowledge").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/knowledge", json={"type": "work", "claim": "Synthetic fact"},
                                          headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)
        unauthenticated.close()

    def test_rejects_invalid_fact_before_database(self):
        with patch("app.api.knowledge.context", side_effect=AssertionError("database should not be reached")):
            response = self.client.post("/api/v1/knowledge", json={"type": "unknown", "claim": "Synthetic fact"},
                                        headers={"Origin": "https://127.0.0.1:8765", "X-CSRF-Token": self.csrf})
            self.assertEqual(response.status_code, 422)
