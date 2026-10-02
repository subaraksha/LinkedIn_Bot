import unittest

from app.domain.telegram import OwnerBinding, authorized_text


class TelegramAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binding = OwnerBinding("1", "20", "20", 1)
        self.update = {
            "update_id": 9,
            "message": {
                "message_id": 4,
                "from": {"id": 20, "is_bot": False},
                "chat": {"id": 20, "type": "private"},
                "text": "PUBLISH W1 V2 CODE",
            },
        }

    def test_only_direct_owner_message_is_accepted(self) -> None:
        self.assertIsNotNone(authorized_text(self.update, self.binding))
        for change in (
            {"edited_message": self.update["message"]},
            {"message": {**self.update["message"], "forward_origin": {}}},
            {"message": {**self.update["message"], "from": {"id": 21, "is_bot": False}}},
            {"message": {**self.update["message"], "chat": {"id": 20, "type": "group"}}},
            {"message": {**self.update["message"], "from": {"id": 20, "is_bot": True}}},
        ):
            self.assertIsNone(authorized_text({"update_id": 9, **change}, self.binding))
