import tempfile
import unittest
import hashlib
import json
from pathlib import Path

from app.storage.installation import InstallationError, installation_identity


class InstallationBindingTests(unittest.TestCase):
    def test_identity_persists_and_rejects_different_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = installation_identity(path, "mongodb://example", "owner")
            self.assertEqual(first, installation_identity(path, "mongodb://example", "owner"))
            self.assertEqual((path / "installation.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(InstallationError):
                installation_identity(path, "mongodb://example", "other")

    def test_password_rotation_keeps_identity_but_host_change_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = installation_identity(path, "mongodb+srv://owner:old@example.net/?retryWrites=true", "owner")
            self.assertEqual(first, installation_identity(
                path, "mongodb+srv://owner:new@example.net/?retryWrites=false", "owner"
            ))
            with self.assertRaises(InstallationError):
                installation_identity(path, "mongodb+srv://owner:new@elsewhere.net/", "owner")

    def test_legacy_binding_migrates_without_changing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            uri = "mongodb+srv://owner:old@example.net/"
            (path / "installation.json").write_text(json.dumps({
                "id": "existing-id",
                "destination_fingerprint": hashlib.sha256(f"{uri}\0owner".encode()).hexdigest(),
            }))
            self.assertEqual(installation_identity(path, uri, "owner"), "existing-id")
            self.assertEqual(installation_identity(
                path, "mongodb+srv://owner:new@example.net/", "owner"
            ), "existing-id")
