"""Only supported, bounded model suggestions may become pending facts."""

import unittest

from app.services.knowledge_extraction import ExtractionResult, validate_suggestions


class ExtractionValidationTests(unittest.TestCase):
    def test_drops_invented_quotes_and_duplicate_claims(self):
        source = "I built a small demo application. I learned Python."
        proposals = ExtractionResult.model_validate({"facts": [
            {"type": "project", "claim": "Built a demo application", "quote": "I built a small demo application."},
            {"type": "project", "claim": "Built a demo application", "quote": "I built a small demo application."},
            {"type": "work", "claim": "Led a large team", "quote": "Led a large team"},
        ]})
        accepted = validate_suggestions(proposals, source)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].type, "project")

    def test_rejects_oversized_claim(self):
        with self.assertRaises(ValueError):
            ExtractionResult.model_validate({"facts": [{"type": "work", "claim": "x" * 2001,
                                                     "quote": "A"}]})
