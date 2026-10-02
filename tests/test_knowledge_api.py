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

    def test_exports_require_csrf_and_reject_public_originals(self):
        unauthenticated = TestClient(app, base_url="https://127.0.0.1:8765")
        self.assertEqual(unauthenticated.get("/api/v1/knowledge/export/status").status_code, 401)
        unauthenticated.close()
        self.assertEqual(self.client.post("/api/v1/knowledge/exports", json={"scope": "public"},
                                          headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)
        with patch("app.api.knowledge.context", side_effect=AssertionError("database should not be reached")):
            response = self.client.post("/api/v1/knowledge/exports",
                                        json={"scope": "public", "include_originals": True},
                                        headers={"Origin": "https://127.0.0.1:8765",
                                                 "X-CSRF-Token": self.csrf})
            self.assertEqual(response.status_code, 422)

    def test_owner_profile_and_questions_require_session_and_csrf(self):
        unauthenticated = TestClient(app, base_url="https://127.0.0.1:8765")
        self.assertEqual(unauthenticated.get("/api/v1/owner/profile").status_code, 401)
        self.assertEqual(unauthenticated.get("/api/v1/clarifications").status_code, 401)
        unauthenticated.close()
        self.assertEqual(self.client.patch("/api/v1/owner/profile", json={"expected_revision": 0, "data": {}},
                                           headers={"Origin": "https://127.0.0.1:8765"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/clarifications/audience/response",
                                          json={"expected_revision": 0, "answer": " "},
                                          headers={"Origin": "https://127.0.0.1:8765",
                                                   "X-CSRF-Token": self.csrf}).status_code, 422)
        with patch("app.api.knowledge.context", side_effect=AssertionError("database should not be reached")):
            response = self.client.patch("/api/v1/owner/profile",
                                         json={"expected_revision": 0, "data": {"tone": "unsupported"}},
                                         headers={"Origin": "https://127.0.0.1:8765",
                                                  "X-CSRF-Token": self.csrf})
            self.assertEqual(response.status_code, 422)
