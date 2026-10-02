import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.security import COOKIE, sessions
from app.api.connections import oauth_states
from app.integrations.linkedin import OAuthToken
from app.main import app


class DashboardSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, base_url="https://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()

    def test_one_use_bootstrap_and_csrf(self) -> None:
        token = sessions.bootstrap_token
        response = self.client.get(f"/bootstrap?token={token}", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get(f"/bootstrap?token={token}").status_code, 401)
        csrf = self.client.get("/api/session").json()["csrf"]
        self.assertTrue(csrf)
        self.assertEqual(self.client.post("/api/session/logout", headers={
            "Origin": "https://127.0.0.1:8765"
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/session/logout", headers={
            "Origin": "https://evil.invalid", "X-CSRF-Token": csrf,
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/session/logout", headers={
            "Origin": "https://127.0.0.1:8765", "X-CSRF-Token": csrf,
        }).status_code, 204)
        self.assertEqual(self.client.get("/api/session").status_code, 401)

    def test_untrusted_host_and_unauthed_dashboard(self) -> None:
        response = self.client.get("/", headers={"Host": "evil.invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/", headers={"Host": "localhost:8765"}).status_code, 400)
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_oauth_callback_requires_matching_session_and_one_use_state(self) -> None:
        session = sessions.new_session()
        self.client.cookies.set(COOKIE, session.id)
        oauth_states["test-state"] = (session.id, time.monotonic() + 60)
        settings = SimpleNamespace(
            linkedin_client_id="client", linkedin_client_secret="secret",
            linkedin_redirect_uri="https://127.0.0.1:8765/api/v1/connections/linkedin/callback",
            mongodb_uri="mongodb://example", mongodb_database="owner",
        )
        with patch("app.api.connections.context", return_value=(settings, "test-installation")), \
             patch("app.api.connections.exchange_code_details", new_callable=AsyncMock) as exchange, \
             patch("app.api.connections.member_identity", new_callable=AsyncMock) as identity, \
             patch("app.api.connections.save_connection", new_callable=AsyncMock) as save:
            exchange.return_value = OAuthToken("synthetic-token", 3600, "openid profile w_member_social")
            identity.return_value = {"sub": "synthetic-member", "name": "Test"}
            wrong = self.client.get(
                "/api/v1/connections/linkedin/callback?state=wrong&code=synthetic",
                follow_redirects=False,
            )
            self.assertEqual(wrong.status_code, 400)
            accepted = self.client.get(
                "/api/v1/connections/linkedin/callback?state=test-state&code=synthetic",
                follow_redirects=False,
            )
            self.assertEqual(accepted.status_code, 303)
            self.assertEqual(save.await_count, 1)
            repeated = self.client.get(
                "/api/v1/connections/linkedin/callback?state=test-state&code=synthetic",
                follow_redirects=False,
            )
            self.assertEqual(repeated.status_code, 400)
