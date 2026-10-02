"""Deterministic question and drafting privacy rules."""

import unittest

from app.domain.owner_profile import OwnerProfileInput
from app.services.draft_context import blocked_terms, build_draft_context
from app.services.guided_questions import derive_questions, merge_questions


class GuidedProfileTests(unittest.TestCase):
    def test_questions_identify_missing_goals_and_ambiguous_work(self):
        facts = [{"_id": "synthetic-1", "type": "project", "claim": "We explored a new database",
                  "status": "pending_confirmation", "publication_permission": "private"}]
        questions = derive_questions({}, facts)
        ids = {item["id"] for item in questions}
        self.assertIn("target_roles", ids)
        self.assertIn("audience", ids)
        self.assertIn("interests", ids)
        self.assertIn("contribution:synthetic-1", ids)
        self.assertIn("experience_kind:synthetic-1", ids)
        skipped = merge_questions(questions, [{"_id": "target_roles", "status": "skipped", "revision": 1}])
        self.assertEqual(next(item for item in skipped if item["id"] == "target_roles")["status"], "skipped")

    def test_draft_context_excludes_private_pending_and_boundaries(self):
        data = OwnerProfileInput(target_roles=["Backend engineer"],
                                 confidential_details=["PRIVATE_CANARY"],
                                 avoid_phrases=["Never say this"], tone="technical").model_dump()
        snapshot = {"revision": 8, "profile": data, "entries": [
            {"_id": "one", "type": "project", "claim": "Public project", "status": "confirmed", "publication_permission": "public"},
            {"_id": "two", "type": "project", "claim": "PRIVATE_CANARY", "status": "confirmed", "publication_permission": "private"},
            {"_id": "three", "type": "tool", "claim": "Pending tool", "status": "pending_confirmation", "publication_permission": "public"},
        ]}
        context = build_draft_context(snapshot)
        self.assertEqual([fact["claim"] for fact in context.facts], ["Public project"])
        self.assertEqual(context.style["tone"], "technical")
        self.assertNotIn("PRIVATE_CANARY", repr(context))
        self.assertEqual(blocked_terms("The private_canary is here", data), ["PRIVATE_CANARY"])
        self.assertEqual(blocked_terms("Never say this in a post", data), ["Never say this"])
        self.assertEqual(blocked_terms("A safe post", data), [])
