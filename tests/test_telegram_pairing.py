import unittest

from app.integrations.telegram_pairing import challenge_digest, pairing_candidate


class TelegramPairingTests(unittest.TestCase):
    def test_only_exact_direct_private_start_can_be_candidate(self) -> None:
        message = {
            "message_id": 8,
            "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
            "chat": {"id": 42, "type": "private"},
            "text": "/start abc123",
        }
        digest = challenge_digest("abc123")
        candidate = pairing_candidate({"update_id": 3, "message": message}, digest)
        self.assertEqual(candidate["sender_id"], "42")
        for update in (
            {"edited_message": message},
            {"message": {**message, "text": "/start other"}},
            {"message": {**message, "forward_origin": {}}},
            {"message": {**message, "chat": {"id": 42, "type": "group"}}},
            {"message": {**message, "from": {"id": 42, "is_bot": True}}},
        ):
            self.assertIsNone(pairing_candidate(update, digest))
