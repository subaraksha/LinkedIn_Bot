"""Opt-in end-to-end owner profile test in a disposable MongoDB database."""

import io
import json
import os
import secrets
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.config import get_settings
from app.domain.owner_profile import OwnerProfileInput
from app.integrations.telegram_pairing import mongo_client
from app.services.draft_context import blocked_terms, build_draft_context
from app.services.knowledge_export import generate_export, read_snapshot
from app.services.knowledge_extraction import SuggestedFact
from app.storage.foundation import ensure_foundation
from app.storage.knowledge import add_text_source, save_suggestions, update_fact
from app.storage.owner_profile import (
    ProfileConflict, get_owner_profile, list_questions, respond_question, save_owner_profile,
)


@unittest.skipUnless(os.getenv("RUN_MONGO_INTEGRATION") == "1", "requires disposable MongoDB test")
class OwnerProfileIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_goals_clarifications_context_and_public_export(self):
        client = mongo_client(get_settings().mongodb_uri)
        name = "profile_test_" + secrets.token_hex(8)
        db = client[name]
        try:
            await ensure_foundation(db, "synthetic-installation")
            source = await add_text_source(db, "synthetic-installation", "Synthetic source",
                                           "We explored a demonstration database project.")
            await save_suggestions(db, "synthetic-installation", source["_id"], source["content_hash"],
                                   [SuggestedFact(type="project", claim="We explored a demonstration database project",
                                                  quote="We explored a demonstration database project.")])
            questions = await list_questions(db, "synthetic-installation")
            ids = {item["id"] for item in questions}
            self.assertIn("target_roles", ids)
            self.assertTrue(any(item.startswith("contribution:") for item in ids))
            await respond_question(db, "synthetic-installation", "public_story", 0, None, True)
            skipped = await list_questions(db, "synthetic-installation")
            self.assertEqual(next(item for item in skipped if item["id"] == "public_story")["status"], "skipped")
            await respond_question(db, "synthetic-installation", "public_story", 1,
                                   "I built a synthetic side project", False)
            story = await db.knowledge_entries.find_one({"claim": "I built a synthetic side project"})
            self.assertEqual(story["status"], "pending_confirmation")
            self.assertEqual(story["publication_permission"], "private")
            await respond_question(db, "synthetic-installation", "target_roles", 0,
                                   "Backend engineer", False)
            profile = await get_owner_profile(db, "synthetic-installation")
            self.assertEqual(profile["target_roles"], ["Backend engineer"])
            data = OwnerProfileInput(target_roles=profile["target_roles"], audience="Recruiters",
                                     interests=["Databases"], content_goals=["Show technical understanding"],
                                     tone="technical", confidential_details=["PRIVATE_CANARY_DO_NOT_EXPORT"])
            await db.workflows.insert_one({"_id": "synthetic-workflow", "active": True,
                                           "state": "AWAITING_APPROVAL", "revision": 1})
            await db.jobs.insert_one({"_id": "synthetic-publish", "kind": "publish", "status": "pending"})
            saved = await save_owner_profile(db, "synthetic-installation", profile["revision"], data)
            workflow = await db.workflows.find_one({"_id": "synthetic-workflow"})
            job = await db.jobs.find_one({"_id": "synthetic-publish"})
            owner = await db.owner_settings.find_one({"_id": "owner"})
            self.assertEqual(workflow["state"], "AWAITING_REVIEW")
            self.assertEqual(job["status"], "blocked_by_profile_change")
            self.assertGreater(owner["preferences_revision"], 0)
            self.assertEqual(saved["tone"], "technical")
            with self.assertRaises(ProfileConflict):
                await save_owner_profile(db, "synthetic-installation", profile["revision"], data)
            question_id = next(item for item in ids if item.startswith("contribution:"))
            await respond_question(db, "synthetic-installation", question_id, 0,
                                   "I implemented the data model", False)
            fact = await db.knowledge_entries.find_one({"source_extraction_id": source["_id"]})
            self.assertEqual(fact["status"], "pending_confirmation")
            self.assertEqual(fact["experience_context"], "I implemented the data model")
            context = build_draft_context(await read_snapshot(db, "synthetic-installation"))
            self.assertEqual(context.facts, ())
            await update_fact(db, "synthetic-installation", fact["_id"], fact["revision"],
                              fact["claim"], fact["experience_context"], "public", "confirmed")
            snapshot = await read_snapshot(db, "synthetic-installation")
            context = build_draft_context(snapshot)
            self.assertEqual(len(context.facts), 1)
            self.assertEqual(context.style["tone"], "technical")
            self.assertNotIn("PRIVATE_CANARY_DO_NOT_EXPORT", repr(context))
            self.assertTrue(blocked_terms("PRIVATE_CANARY_DO_NOT_EXPORT", snapshot["profile"]))
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                full, full_revision = await generate_export(db, "synthetic-installation", root, "full")
                public, public_revision = await generate_export(db, "synthetic-installation", root, "public")
                self.assertEqual(full_revision, public_revision)
                with zipfile.ZipFile(io.BytesIO(full)) as archive:
                    self.assertIn("PRIVATE_CANARY_DO_NOT_EXPORT", archive.read("profile.json").decode())
                with zipfile.ZipFile(io.BytesIO(public)) as archive:
                    for filename in archive.namelist():
                        self.assertNotIn("PRIVATE_CANARY_DO_NOT_EXPORT", archive.read(filename).decode())
                with zipfile.ZipFile(io.BytesIO(full)) as archive:
                    full_profile = json.loads(archive.read("profile.json"))
                with zipfile.ZipFile(io.BytesIO(public)) as archive:
                    public_profile = json.loads(archive.read("profile.json"))
                self.assertEqual(full_profile["goals"]["target_roles"], ["Backend engineer"])
                self.assertTrue(full_profile["clarifications"])
                self.assertEqual(public_profile["facts"][0]["claim"], fact["claim"])
                self.assertEqual(public_profile["clarifications"], [])
                self.assertEqual(public_profile["goals"], {})
        finally:
            await client.drop_database(name)
            await client.close()
