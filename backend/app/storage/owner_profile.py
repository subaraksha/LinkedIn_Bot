"""Single-owner profile and clarification mutations with revision guards."""

from datetime import datetime, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.domain.owner_profile import DEFAULT_PROFILE, OwnerProfileInput
from app.services.guided_questions import derive_questions, merge_questions
from app.storage.knowledge import _bump


class ProfileConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _public_profile(record: dict | None) -> dict:
    if not record:
        return {"revision": 0, "updated_at": None, **DEFAULT_PROFILE.model_dump()}
    return {"revision": record["revision"], "updated_at": record["updated_at"],
            **OwnerProfileInput.model_validate(record["data"]).model_dump()}


async def get_owner_profile(db, installation_id: str) -> dict:
    record = await db.owner_profiles.find_one({"_id": "profile", "installation_id": installation_id})
    return _public_profile(record)


async def save_owner_profile(db, installation_id: str, expected_revision: int,
                             data: OwnerProfileInput) -> dict:
    now = _now()
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                existing = await db.owner_profiles.find_one({"_id": "profile", "installation_id": installation_id}, session=session)
                revision = existing["revision"] if existing else 0
                if revision != expected_revision:
                    raise ProfileConflict("Goals or preferences changed; refresh before saving")
                if existing:
                    result = await db.owner_profiles.update_one(
                        {"_id": "profile", "installation_id": installation_id, "revision": revision},
                        {"$set": {"data": data.model_dump(), "updated_at": now}, "$inc": {"revision": 1}},
                        session=session)
                    if result.matched_count != 1:
                        raise ProfileConflict("Goals or preferences changed; refresh before saving")
                else:
                    await db.owner_profiles.insert_one({"_id": "profile", "installation_id": installation_id,
                        "schema_version": 1, "revision": 1, "data": data.model_dump(),
                        "created_at": now, "updated_at": now}, session=session)
                await _bump(db, installation_id, session, now, component="preferences")
    except DuplicateKeyError:
        raise ProfileConflict("Goals or preferences changed; refresh before saving") from None
    return {"revision": expected_revision + 1, "updated_at": now, **data.model_dump()}


async def list_questions(db, installation_id: str) -> list[dict]:
    profile = await get_owner_profile(db, installation_id)
    facts = await db.knowledge_entries.find(
        {"installation_id": installation_id, "status": {"$ne": "deleted"}}
    ).limit(10000).to_list(length=10000)
    saved = await db.clarifications.find({"installation_id": installation_id}).limit(10000).to_list(length=10000)
    return merge_questions(derive_questions(profile, facts), saved)


async def respond_question(db, installation_id: str, question_id: str,
                           expected_revision: int, answer: str | None, skip: bool) -> dict:
    if not skip and (not answer or not answer.strip() or len(answer) > 2000):
        raise ProfileConflict("Enter an answer of at most 2000 characters")
    now = _now()
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                profile_record = await db.owner_profiles.find_one({"_id": "profile", "installation_id": installation_id}, session=session)
                profile = _public_profile(profile_record)
                facts = await db.knowledge_entries.find(
                    {"installation_id": installation_id, "status": {"$ne": "deleted"}},
                    session=session).limit(10000).to_list(length=10000)
                current = next((item for item in derive_questions(profile, facts) if item["id"] == question_id), None)
                if not current:
                    raise ProfileConflict("Question is no longer relevant; refresh")
                existing = await db.clarifications.find_one({"_id": question_id, "installation_id": installation_id}, session=session)
                revision = existing["revision"] if existing else 0
                if revision != expected_revision:
                    raise ProfileConflict("Question changed; refresh before saving")
                if (current["kind"] == "public_story" and existing
                        and existing.get("status") == "answered" and not skip):
                    raise ProfileConflict("This answer already created a pending fact; edit that fact instead")
                status = "skipped" if skip else "answered"
                record = {"_id": question_id, "installation_id": installation_id,
                          "schema_version": 1, "kind": current["kind"], "fact_id": current["fact_id"],
                          "prompt": current["prompt"], "status": status,
                          "answer": None if skip else answer.strip(), "revision": revision + 1,
                          "updated_at": now, "created_at": existing["created_at"] if existing else now}
                if existing:
                    result = await db.clarifications.replace_one(
                        {"_id": question_id, "installation_id": installation_id,
                         "revision": revision}, record, session=session)
                    if result.matched_count != 1:
                        raise ProfileConflict("Question changed; refresh before saving")
                else:
                    await db.clarifications.insert_one(record, session=session)
                if not skip and current["kind"] in {"target_roles", "audience", "interests"}:
                    data = OwnerProfileInput.model_validate({key: profile[key]
                        for key in OwnerProfileInput.model_fields})
                    if current["kind"] == "audience":
                        data.audience = answer.strip()
                    else:
                        values = [part.strip() for part in answer.replace("\n", ",").split(",") if part.strip()]
                        if not values:
                            raise ProfileConflict("Enter at least one item")
                        setattr(data, current["kind"], values)
                        data = OwnerProfileInput.model_validate(data.model_dump())
                    if profile_record:
                        await db.owner_profiles.update_one(
                            {"_id": "profile", "installation_id": installation_id,
                             "revision": profile_record["revision"]},
                            {"$set": {"data": data.model_dump(), "updated_at": now}, "$inc": {"revision": 1}},
                            session=session)
                    else:
                        await db.owner_profiles.insert_one({"_id": "profile", "installation_id": installation_id,
                            "schema_version": 1, "revision": 1, "data": data.model_dump(),
                            "created_at": now, "updated_at": now}, session=session)
                    component = "preferences"
                else:
                    component = "knowledge"
                    if not skip and current["kind"] in {"contribution", "experience_kind", "public_story"}:
                        source_id = str(uuid4())
                        await db.sources.insert_one({
                            "_id": source_id, "installation_id": installation_id, "schema_version": 1,
                            "kind": "owner_statement", "label": "Clarification answer", "content": answer.strip(),
                            "source_version": 1, "revision": 1, "created_at": now, "updated_at": now,
                        }, session=session)
                        if current["kind"] == "public_story":
                            await db.knowledge_entries.insert_one({
                                "_id": str(uuid4()), "installation_id": installation_id, "schema_version": 1,
                                "type": "other", "claim": answer.strip(), "experience_context": "",
                                "status": "pending_confirmation", "publication_permission": "private",
                                "evidence": [{"source_id": source_id, "kind": "owner_statement"}],
                                "revision": 1, "created_at": now, "updated_at": now,
                            }, session=session)
                        else:
                            fact = next((item for item in facts if item["_id"] == current["fact_id"]), None)
                            if not fact or fact["status"] != "pending_confirmation":
                                raise ProfileConflict("Linked fact changed; refresh")
                            update = await db.knowledge_entries.update_one(
                                {"_id": fact["_id"], "installation_id": installation_id,
                                 "revision": fact["revision"], "status": "pending_confirmation"},
                                {"$set": {"experience_context": answer.strip(), "updated_at": now},
                                 "$push": {"evidence": {"source_id": source_id, "kind": "owner_statement"}},
                                 "$inc": {"revision": 1}}, session=session)
                            if update.matched_count != 1:
                                raise ProfileConflict("Linked fact changed; refresh")
                await _bump(db, installation_id, session, now, component=component)
    except DuplicateKeyError:
        raise ProfileConflict("Question changed; refresh before saving") from None
    return {"id": question_id, "status": status, "answer": record["answer"], "revision": revision + 1}
