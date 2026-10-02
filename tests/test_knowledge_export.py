"""Portable profile bundles with strict public privacy projection."""

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from app.services.knowledge_export import (
    SCHEMA, ExportError, _save_bundle, build_bundle, project_profile,
    verify_bundle,
)

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
PUBLIC = "Built a synthetic public project"
PRIVATE = "PRIVATE_CANARY_DO_NOT_EXPORT"


def snapshot():
    return {"revision": 7, "updated_at": NOW,
            "sources": [
                {"_id": "11111111-1111-4111-8111-111111111111", "kind": "resume_txt",
                 "label": "Private resume", "content_hash": "a" * 64, "source_version": 1,
                 "parser_version": 1, "content": PRIVATE},
                {"_id": "22222222-2222-4222-8222-222222222222", "kind": "owner_statement",
                 "label": "Manual entry", "source_version": 1, "content": PRIVATE},
            ],
            "entries": [
                {"_id": "33333333-3333-4333-8333-333333333333", "type": "project",
                 "claim": PUBLIC, "status": "confirmed", "publication_permission": "public",
                 "experience_context": "", "revision": 2, "updated_at": NOW,
                 "evidence": [{"source_id": "11111111-1111-4111-8111-111111111111",
                               "kind": "resume_txt", "quote": "synthetic public project", "location": "Document"}]},
                {"_id": "44444444-4444-4444-8444-444444444444", "type": "work",
                 "claim": PRIVATE, "status": "confirmed", "publication_permission": "private",
                 "experience_context": PRIVATE, "revision": 1, "updated_at": NOW,
                 "evidence": [{"source_id": "22222222-2222-4222-8222-222222222222",
                               "kind": "owner_statement", "quote": PRIVATE}]},
                {"_id": "55555555-5555-4555-8555-555555555555", "type": "tool",
                 "claim": "Pending tool", "status": "pending_confirmation", "publication_permission": "public",
                 "experience_context": "", "revision": 1, "updated_at": NOW, "evidence": []},
                {"_id": "66666666-6666-4666-8666-666666666666", "status": "deleted",
                 "claim_hash": "b" * 64, "source_extraction_id": "11111111-1111-4111-8111-111111111111"},
            ]}


class ExportTests(unittest.TestCase):
    def test_full_and_public_bundle_are_independently_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            for scope in ("full", "public"):
                profile = project_profile(snapshot(), scope, NOW)
                bundle = build_bundle(profile, snapshot(), Path(directory))
                manifest = verify_bundle(bundle)
                self.assertEqual(manifest["profile_revision"], 7)
                with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                    names = set(archive.namelist())
                    self.assertEqual(names, {"profile.json", "profile.md", "profile.schema.json", "manifest.json"})
                    external = json.loads(archive.read("profile.json"))
                    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(external)
                    sources = {item["id"] for item in external["sources"]}
                    for fact in external["facts"]:
                        for evidence in fact["evidence"]:
                            self.assertIn(evidence["source_id"], sources)
                if scope == "public":
                    self.assertNotIn(PRIVATE.encode(), bundle)
                    self.assertEqual([item["claim"] for item in profile["facts"]], [PUBLIC])
                    self.assertEqual(profile["sources"], [])
                    self.assertEqual(profile["suppression_markers"], [])
                else:
                    self.assertEqual(len(profile["facts"]), 3)
                    self.assertEqual(len(profile["suppression_markers"]), 1)

    def test_originals_are_explicit_and_full_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "uploads").mkdir()
            raw = b"Synthetic resume content only"
            (root / "uploads" / "synthetic.upload").write_bytes(raw)
            data = snapshot()
            data["sources"][0]["local_relative_path"] = "uploads/synthetic.upload"
            data["sources"][0]["content_hash"] = hashlib.sha256(raw).hexdigest()
            profile = project_profile(data, "full", NOW)
            bundle = build_bundle(profile, data, root, include_originals=True)
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                name = "sources/11111111-1111-4111-8111-111111111111.txt"
                self.assertEqual(archive.read(name), raw)
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["files"][name]["source_id"], data["sources"][0]["_id"])
            with self.assertRaises(ExportError):
                build_bundle(project_profile(data, "public", NOW), data, root, include_originals=True)

    def test_explicit_boundary_overrides_public_fact_permission(self):
        data = snapshot()
        data["profile"] = {"confidential_details": [PUBLIC]}
        profile = project_profile(data, "public", NOW)
        self.assertEqual(profile["facts"], [])
        self.assertNotIn(PUBLIC, json.dumps(profile))

    def test_missing_evidence_source_is_rejected(self):
        data = snapshot()
        data["sources"] = []
        with self.assertRaises(ExportError):
            project_profile(data, "full", NOW)

    def test_failed_replace_preserves_old_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = project_profile(snapshot(), "full", NOW)
            bundle = build_bundle(profile, snapshot(), root)
            _save_bundle(root, "full", bundle, 7)
            target = root / "exports" / "full-knowledge.zip"
            before = target.read_bytes()
            newer = dict(profile, profile_revision=8)
            replacement = build_bundle(newer, snapshot(), root)
            with patch("app.services.knowledge_export.os.replace", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    _save_bundle(root, "full", replacement, 8)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(verify_bundle(target.read_bytes())["profile_revision"], 7)
