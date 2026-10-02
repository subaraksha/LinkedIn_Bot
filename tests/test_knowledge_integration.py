"""Opt-in professional intake checks in a disposable database."""

import os
import secrets
import unittest

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.storage.foundation import ensure_foundation
from app.services.knowledge_extraction import SuggestedFact
from app.storage.knowledge import (
    KnowledgeConflict, add_manual_fact, add_text_source, delete_fact, list_knowledge, save_suggestions, update_fact,
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
            fact = await add_manual_fact(db, "test-installation", "project", "Built a sample app", "", "private")
            self.assertEqual(fact["publication_permission"], "private")
            current = await list_knowledge(db, "test-installation")
            self.assertEqual(current["profile_revision"], 3)
            self.assertEqual(len(current["facts"]), 2)
            self.assertTrue(any(item["_id"] == fact["evidence"][0]["source_id"] for item in current["sources"]))
            with self.assertRaises(KnowledgeConflict):
                await update_fact(db, "test-installation", fact["_id"], 9, "Changed", "", "public", "confirmed")
            changed = await update_fact(db, "test-installation", fact["_id"], 1, "Built a revised sample app", "", "public", "confirmed")
            self.assertEqual(changed["revision"], 2)
            self.assertNotEqual(changed["evidence"][0]["source_id"], fact["evidence"][0]["source_id"])
            await delete_fact(db, "test-installation", fact["_id"], 2)
            current = await list_knowledge(db, "test-installation")
            self.assertEqual(current["profile_revision"], 5)
            self.assertEqual(len(current["facts"]), 1)
            marker = await db.knowledge_entries.find_one({"_id": fact["_id"]})
            self.assertEqual(marker["status"], "deleted")
            self.assertNotIn("claim", marker)
        finally:
            await client.drop_database(name)
            await client.close()
