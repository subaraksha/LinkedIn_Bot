"""Opt-in professional intake checks in a disposable database."""

import os
import secrets
import tempfile
from pathlib import Path
import unittest

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.storage.foundation import ensure_foundation
from app.services.knowledge_extraction import SuggestedFact
from app.services.knowledge_export import export_status, generate_export, verify_bundle
from app.storage.knowledge import (
    KnowledgeConflict, add_manual_fact, add_resume_source, add_text_source, delete_fact, list_knowledge, save_suggestions, update_fact,
)


@unittest.skipUnless(os.getenv("RUN_MONGO_INTEGRATION") == "1", "requires disposable MongoDB test")
class KnowledgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_provenance_revisions_and_deletion(self):
        client = mongo_client(get_settings().mongodb_uri)
        name = "kb_test_" + secrets.token_hex(8)
        db = client[name]
        try:
            await ensure_foundation(db, "test-installation")
            source = await add_text_source(db, "test-installation", "Profile", "Synthetic profile text")
            repeated = await add_text_source(db, "test-installation", "Profile", "Synthetic profile text")
            self.assertEqual(source["_id"], repeated["_id"])
            proposal = SuggestedFact(type="tool", claim="Used a sample tool", quote="Synthetic profile text")
            first = await save_suggestions(db, "test-installation", source["_id"], source["content_hash"], [proposal])
            self.assertEqual(first["created"], 1)
            repeated_extraction = await save_suggestions(db, "test-installation", source["_id"], source["content_hash"], [proposal])
            self.assertTrue(repeated_extraction["already_extracted"])
            pending = await db.knowledge_entries.find_one({"source_extraction_id": source["_id"]})
            self.assertEqual(pending["status"], "pending_confirmation")
            self.assertEqual(pending["publication_permission"], "private")
            self.assertEqual(pending["evidence"][0]["quote"], "Synthetic profile text")
            resume, retained = await add_resume_source(
                db, "test-installation", "resume_pdf", "synthetic-hash",
                "uploads/synthetic.upload", [{"label": "Page 1", "text": "Synthetic candidate built a project."}])
            self.assertTrue(retained)
            resume_proposal = SuggestedFact(type="project", claim="Built a project", quote="built a project")
            saved = await save_suggestions(db, "test-installation", resume["_id"],
                                           resume["content_hash"], [resume_proposal])
            self.assertEqual(saved["created"], 1)
            resume_fact = await db.knowledge_entries.find_one({"source_extraction_id": resume["_id"]})
            self.assertEqual(resume_fact["evidence"][0]["location"], "Page 1")
            fact = await add_manual_fact(db, "test-installation", "project", "Built a sample app", "", "private")
            self.assertEqual(fact["publication_permission"], "private")
            current = await list_knowledge(db, "test-installation")
            self.assertEqual(current["profile_revision"], 5)
            self.assertEqual(len(current["facts"]), 3)
            self.assertTrue(any(item["_id"] == fact["evidence"][0]["source_id"] for item in current["sources"]))
            with self.assertRaises(KnowledgeConflict):
                await update_fact(db, "test-installation", fact["_id"], 9, "Changed", "", "public", "confirmed")
            changed = await update_fact(db, "test-installation", fact["_id"], 1, "Built a revised sample app", "", "public", "confirmed")
            self.assertEqual(changed["revision"], 2)
            self.assertNotEqual(changed["evidence"][0]["source_id"], fact["evidence"][0]["source_id"])
            await delete_fact(db, "test-installation", fact["_id"], 2)
            current = await list_knowledge(db, "test-installation")
            self.assertEqual(current["profile_revision"], 7)
            self.assertEqual(len(current["facts"]), 2)
            marker = await db.knowledge_entries.find_one({"_id": fact["_id"]})
            self.assertEqual(marker["status"], "deleted")
            self.assertNotIn("claim", marker)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                full, revision = await generate_export(db, "test-installation", root, "full")
                public, public_revision = await generate_export(db, "test-installation", root, "public")
                self.assertEqual(revision, current["profile_revision"])
                self.assertEqual(public_revision, revision)
                self.assertEqual(verify_bundle(full)["scope"], "full")
                self.assertEqual(verify_bundle(public)["scope"], "public")
                status = await export_status(db, "test-installation", root)
                self.assertEqual(status["full_status"], "current")
                self.assertEqual(status["public_status"], "current")
        finally:
            await client.drop_database(name)
            await client.close()
