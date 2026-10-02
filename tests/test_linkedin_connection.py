import unittest
from datetime import datetime, timedelta, timezone

from app.storage.linkedin_connection import ConnectionError, _connection_health, _validate_member


class LinkedInConnectionTests(unittest.TestCase):
    def test_expired_or_missing_credential_requires_reconnect(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(
            _connection_health({"expires_at": now - timedelta(seconds=1)}, True, now),
            "reconnect_required",
        )
        self.assertEqual(
            _connection_health({"expires_at": now + timedelta(days=1)}, False, now),
            "reconnect_required",
        )
        self.assertEqual(
            _connection_health({"expires_at": now + timedelta(days=1)}, True, now),
            "connected",
        )

    def test_different_linkedin_member_is_rejected(self):
        _validate_member({"member_id": "owner"}, "owner")
        with self.assertRaises(ConnectionError):
            _validate_member({"member_id": "owner"}, "someone-else")
