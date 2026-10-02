"""A small owner edit must preserve the rest of the reviewed draft."""

import unittest

from app.services.draft_workflow import _literal_revision


class LiteralRevisionTests(unittest.TestCase):
    def test_system_one_edit_changes_only_requested_phrase(self):
        prior = ("Curious to see how fast System 1 inference and joint-embedding "
                 "architectures will shape the future of practical AI systems. 💡 "
                 "What are your thoughts on where this balance is heading? 🚀 "
                 "#AI #MachineLearning #DeepLearning")
        self.assertEqual(
            _literal_revision(prior, "Make it System One instead of System 1"),
            prior.replace("System 1", "System One"),
        )

    def test_ambiguous_or_broad_requests_use_generation_path(self):
        self.assertIsNone(_literal_revision("System 1 and System 1",
                                            "Make it System One instead of System 1"))
        self.assertIsNone(_literal_revision("System 1", "Make it shorter"))
        self.assertIsNone(_literal_revision("System 12",
                                            "Make it System One instead of System 1"))
