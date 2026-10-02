"""Owner supplied professional sources and facts."""

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError


class KnowledgeConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _public_source(item):
    result = {key: item[key] for key in ("_id", "kind", "label", "content_hash", "extraction_status", "revision", "created_at", "updated_at") if key in item}
    result["content"] = item.get("content", "")[:1000]
    return result


def _public_fact(item):
    return {key: item[key] for key in ("_id", "type", "claim", "experience_context", "status", "publication_permission", "evidence", "revision", "created_at", "updated_at") if key in item}


async def _bump(db, installation_id, session, now, *, component="knowledge"):
    owner = await db.owner_settings.update_one(
        {"_id": "owner", "installation_id": installation_id},
        {"$inc": {"profile_revision": 1,
                  "preferences_revision" if component == "preferences" else "knowledge_revision": 1,
                  "revision": 1},
         "$set": {"updated_at": now}}, session=session,
    )
    if owner.matched_count != 1:
        raise KnowledgeConflict("Owner database binding changed")
    await db.workflows.update_many(
        {"active": True, "state": {"$in": ["AWAITING_APPROVAL", "PUBLISH_PENDING"]}},
        {"$set": {"state": "AWAITING_REVIEW", "pending_preview.status": "invalidated_by_profile_change", "updated_at": now},
         "$inc": {"revision": 1}}, session=session,
    )
    await db.jobs.update_many(
        {"kind": "publish", "status": "pending"},
        {"$set": {"status": "blocked_by_profile_change", "updated_at": now}}, session=session,
    )


async def add_text_source(db, installation_id: str, label: str, content: str):
    content = content.strip().replace("\r\n", "\n")
    digest = hashlib.sha256(content.encode()).hexdigest()
    existing = await db.sources.find_one({"installation_id": installation_id, "kind": "linkedin_profile_text", "content_hash": digest})
    if existing:
        return _public_source(existing)
    now = _now()
    record = {"_id": str(uuid4()), "installation_id": installation_id, "schema_version": 1,
              "kind": "linkedin_profile_text", "label": label.strip(), "content": content,
              "content_hash": digest, "extraction_status": "not_started", "source_version": 1,
              "revision": 1, "created_at": now, "updated_at": now}
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                await db.sources.insert_one(record, session=session)
                await _bump(db, installation_id, session, now)
    except DuplicateKeyError:
        existing = await db.sources.find_one({"installation_id": installation_id, "kind": "linkedin_profile_text", "content_hash": digest})
        if existing:
            return _public_source(existing)
        raise KnowledgeConflict("Source already exists") from None
    return _public_source(record)


async def add_resume_source(db, installation_id: str, kind: str, digest: str,
                            relative_path: str, sections: list[dict[str, str]]):
    existing = await db.sources.find_one({"installation_id": installation_id,
                                          "kind": kind, "content_hash": digest})
    if existing:
        return _public_source(existing), False
    now = _now()
    content = "\n\n".join(f"[{section['label']}]\n{section['text']}" for section in sections)
    record = {"_id": str(uuid4()), "installation_id": installation_id, "schema_version": 1,
              "kind": kind, "label": "Resume", "content": content, "sections": sections,
              "local_relative_path": relative_path, "content_hash": digest,
              "parser_version": 1, "source_version": 1, "extraction_status": "not_started",
              "revision": 1, "created_at": now, "updated_at": now}
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                await db.sources.insert_one(record, session=session)
                await _bump(db, installation_id, session, now)
    except DuplicateKeyError:
        existing = await db.sources.find_one({"installation_id": installation_id,
                                              "kind": kind, "content_hash": digest})
        if existing:
            return _public_source(existing), False
        raise KnowledgeConflict("Source already exists") from None
    return _public_source(record), True


async def add_manual_fact(db, installation_id: str, fact_type: str, claim: str,
                          experience_context: str, publication_permission: str):
    now = _now()
    claim = claim.strip()
    source_id = str(uuid4())
    source = {"_id": source_id, "installation_id": installation_id, "schema_version": 1,
              "kind": "owner_statement", "label": "Manual entry", "content": claim,
              "source_version": 1, "revision": 1, "created_at": now, "updated_at": now}
    fact = {"_id": str(uuid4()), "installation_id": installation_id, "schema_version": 1,
            "type": fact_type, "claim": claim, "experience_context": experience_context.strip(),
            "status": "confirmed", "publication_permission": publication_permission,
            "evidence": [{"source_id": source_id, "kind": "owner_statement"}],
            "revision": 1, "created_at": now, "updated_at": now}
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            await db.sources.insert_one(source, session=session)
            await db.knowledge_entries.insert_one(fact, session=session)
            await _bump(db, installation_id, session, now)
    return _public_fact(fact)


async def update_fact(db, installation_id: str, fact_id: str, expected_revision: int,
                      claim: str, experience_context: str, publication_permission: str, status: str):
    now = _now()
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            existing = await db.knowledge_entries.find_one(
                {"_id": fact_id, "installation_id": installation_id, "status": {"$ne": "deleted"}}, session=session)
            if not existing:
                raise KnowledgeConflict("Fact was not found")
            if existing["revision"] != expected_revision:
                raise KnowledgeConflict("Fact changed; refresh before saving")
            updated = {"claim": claim.strip(), "experience_context": experience_context.strip(),
                       "publication_permission": publication_permission, "status": status, "updated_at": now}
            if claim.strip() != existing["claim"]:
                source_id = str(uuid4())
                await db.sources.insert_one({"_id": source_id, "installation_id": installation_id, "schema_version": 1,
                    "kind": "owner_statement", "label": "Manual correction", "content": claim.strip(),
                    "source_version": 1, "revision": 1, "created_at": now, "updated_at": now}, session=session)
                updated["evidence"] = [{"source_id": source_id, "kind": "owner_statement"}]
            result = await db.knowledge_entries.update_one(
                {"_id": fact_id, "installation_id": installation_id, "revision": expected_revision},
                {"$set": updated, "$inc": {"revision": 1}}, session=session)
            if result.matched_count != 1:
                raise KnowledgeConflict("Fact changed; refresh before saving")
            await _bump(db, installation_id, session, now)
            existing.update(updated)
            existing["revision"] += 1
    return _public_fact(existing)


async def delete_fact(db, installation_id: str, fact_id: str, expected_revision: int):
    now = _now()
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            existing = await db.knowledge_entries.find_one(
                {"_id": fact_id, "installation_id": installation_id, "status": {"$ne": "deleted"}}, session=session)
            if not existing:
                raise KnowledgeConflict("Fact was not found")
            if existing["revision"] != expected_revision:
                raise KnowledgeConflict("Fact changed; refresh before deleting")
            result = await db.knowledge_entries.update_one(
                {"_id": fact_id, "installation_id": installation_id, "revision": expected_revision},
                {"$set": {"status": "deleted", "claim_hash": hashlib.sha256(existing["claim"].casefold().encode()).hexdigest(),
                          "deleted_at": now, "updated_at": now},
                 "$unset": {"claim": "", "experience_context": "", "evidence": ""},
                 "$inc": {"revision": 1}}, session=session)
            if result.matched_count != 1:
                raise KnowledgeConflict("Fact changed; refresh before deleting")
            await _bump(db, installation_id, session, now)
    return {"deleted": True}


async def list_knowledge(db, installation_id: str):
    sources = await db.sources.find({"installation_id": installation_id}).sort("created_at", -1).limit(100).to_list(length=100)
    facts = await db.knowledge_entries.find({"installation_id": installation_id, "status": {"$ne": "deleted"}}).sort("created_at", -1).limit(200).to_list(length=200)
    owner = await db.owner_settings.find_one({"_id": "owner", "installation_id": installation_id})
    return {"profile_revision": owner.get("profile_revision", 0) if owner else 0,
            "sources": [_public_source(item) for item in sources],
            "facts": [_public_fact(item) for item in facts]}


async def save_suggestions(db, installation_id: str, source_id: str, source_hash: str, suggestions):
    """Commit validated proposals once per immutable source; owner edits always win."""
    now = _now()
    async with db.client.start_session() as session:
        async with await session.start_transaction():
            source = await db.sources.find_one(
                {"_id": source_id, "installation_id": installation_id, "kind": {"$in": ["linkedin_profile_text", "resume_pdf", "resume_docx", "resume_txt"]},
                 "content_hash": source_hash}, session=session)
            if not source:
                raise KnowledgeConflict("Source changed or was not found")
            if source.get("extraction_status") == "completed":
                return {"created": 0, "already_extracted": True}
            old = await db.knowledge_entries.find({
                "installation_id": installation_id, "source_extraction_id": source_id,
            }, session=session).to_list(length=200)
            suppressed = {item.get("claim_hash") for item in old if item.get("status") == "deleted"}
            existing = {item.get("source_claim_hash") for item in old}
            created = 0
            for item in suggestions:
                digest = hashlib.sha256(item.claim.casefold().encode()).hexdigest()
                if digest in suppressed or digest in existing:
                    continue
                location = next((part["label"] for part in source.get("sections", [])
                                 if item.quote in part["text"]), None)
                if source["kind"].startswith("resume_") and location is None:
                    continue
                fact = {"_id": str(uuid4()), "installation_id": installation_id, "schema_version": 1,
                        "source_extraction_id": source_id, "source_claim_hash": digest,
                        "type": item.type, "claim": item.claim,
                        "experience_context": item.experience_context,
                        "status": "pending_confirmation", "publication_permission": "private",
                        "evidence": [{"source_id": source_id, "kind": source["kind"],
                                      "quote": item.quote, "location": location}],
                        "revision": 1, "created_at": now, "updated_at": now}
                await db.knowledge_entries.insert_one(fact, session=session)
                created += 1
            await db.sources.update_one(
                {"_id": source_id, "installation_id": installation_id},
                {"$set": {"extraction_status": "completed", "extracted_at": now, "updated_at": now},
                 "$inc": {"revision": 1}}, session=session)
            await _bump(db, installation_id, session, now)
    return {"created": created, "already_extracted": False}
