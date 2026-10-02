import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.domain.approval import PublicationEnvelope
from app.domain.preview import PendingPreview, approve_preview
from app.domain.telegram import InboundText, OwnerBinding


class PreviewApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.envelope = PublicationEnvelope(
            draft_id="draft-1", draft_version=2,
            text="A test post 🙂\nhttps://example.com #AI",
            author_urn="urn:li:person:owner",
        )
        self.binding = OwnerBinding("bot", "sender", "chat", 1)
        self.message = InboundText(12, "message-7", "sender", "chat", "PUBLISH W1 V2 CODE123")
        self.pending = PendingPreview(
            workflow_code="W1", challenge_code="CODE123",
            envelope_hash=self.envelope.digest(), draft_version=2,
            author_urn=self.envelope.author_urn, bot_id="bot",
            sender_id="sender", chat_id="chat", binding_revision=1,
            receiver_epoch=3, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            body_delivery_confirmed=True, control_delivery_confirmed=True,
        )

    def test_exact_delivered_preview_authorizes_once(self) -> None:
        receipt = approve_preview(self.pending, self.message, self.binding, 3, self.envelope)
        self.assertEqual(receipt.envelope_hash, self.envelope.digest())
        for change in (
            replace(self.pending, consumed=True),
            replace(self.pending, body_delivery_confirmed=False),
            replace(self.pending, control_delivery_confirmed=False),
            replace(self.pending, receiver_epoch=2),
            replace(self.pending, binding_revision=2),
            replace(self.pending, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        ):
            self.assertIsNone(approve_preview(change, self.message, self.binding, 3, self.envelope))

    def test_changed_account_text_or_command_cannot_authorize(self) -> None:
        self.assertIsNone(approve_preview(
            self.pending, self.message, self.binding, 3,
            self.envelope.model_copy(update={"text": self.envelope.text + "!"}),
        ))
        self.assertIsNone(approve_preview(
            self.pending, self.message, self.binding, 3,
            self.envelope.model_copy(update={"author_urn": "urn:li:person:other"}),
        ))
        for text in ("looks good", "PUBLISH W1 V2 WRONG", "PUBLISH W1 V3 CODE123", "PUBLISH W1 V2 CODE123\nthanks"):
            self.assertIsNone(approve_preview(
                self.pending, replace(self.message, text=text), self.binding, 3, self.envelope,
            ))
