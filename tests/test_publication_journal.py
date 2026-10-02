import tempfile
import unittest
from pathlib import Path

from app.storage.publication_journal import load_success, remove_success, save_success


class PublicationJournalTests(unittest.TestCase):
    def test_confirmed_post_id_survives_until_database_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            attempt_id = "attempt:approval:123"
            self.assertIsNone(load_success(data_dir, attempt_id))
            save_success(data_dir, attempt_id, "urn:li:share:123")
            self.assertEqual(load_success(data_dir, attempt_id), "urn:li:share:123")
            self.assertEqual((data_dir / "publication-journal" / f"{attempt_id}.json").stat().st_mode & 0o777, 0o600)
            remove_success(data_dir, attempt_id)
            self.assertIsNone(load_success(data_dir, attempt_id))
