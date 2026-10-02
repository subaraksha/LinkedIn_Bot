import unittest

from app.domain.approval import PublicationEnvelope, parse_publish_command


class ApprovalContractTests(unittest.TestCase):
    def test_command_is_whole_message(self) -> None:
        self.assertIsNotNone(parse_publish_command("PUBLISH W12 V3 ABC123"))
        for text in (
            "Looks good, PUBLISH W12 V3 ABC123",
            '"PUBLISH W12 V3 ABC123"',
            "PUBLISH W12 V3 ABC123\nThanks",
            "PUBLISH W12 V0 ABC123",
            "yes",
        ):
            self.assertIsNone(parse_publish_command(text))

    def test_envelope_digest_changes_with_any_publication_field(self) -> None:
        base = PublicationEnvelope(
            draft_id="draft-1", draft_version=1, text="A post with emoji 🙂",
            author_urn="urn:li:person:owner",
        )
        self.assertEqual(base.canonical_bytes(), base.canonical_bytes())
        for changed in (
            base.model_copy(update={"text": "A post with emoji 🙂!"}),
            base.model_copy(update={"author_urn": "urn:li:person:other"}),
            base.model_copy(update={"visibility": "CONNECTIONS"}),
            base.model_copy(update={"is_reshare_disabled_by_author": True}),
        ):
            self.assertNotEqual(base.digest(), changed.digest())
