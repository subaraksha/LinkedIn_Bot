"""Private local dashboard endpoints for owner supplied professional information."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.connections import bound_database, context
from app.api.security import require_session
from app.services.knowledge_extraction import ExtractionError, suggest_facts
from app.storage.knowledge import (
    KnowledgeConflict, add_manual_fact, add_text_source, delete_fact, list_knowledge, save_suggestions, update_fact,
)

router = APIRouter(prefix="/api/v1")

FactType = Literal["education", "work", "project", "tool", "achievement", "other"]
Permission = Literal["private", "public"]
Status = Literal["confirmed", "disputed", "pending_confirmation"]


class TextSourceInput(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50000)


class FactInput(BaseModel):
    type: FactType
    claim: str = Field(min_length=1, max_length=2000)
    experience_context: str = Field(default="", max_length=2000)
    publication_permission: Permission = "private"


class FactUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    claim: str = Field(min_length=1, max_length=2000)
    experience_context: str = Field(default="", max_length=2000)
    publication_permission: Permission
    status: Status


class FactDelete(BaseModel):
    expected_revision: int = Field(ge=1)


@router.get("/knowledge")
async def get_knowledge(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return await list_knowledge(db, identity)


@router.post("/sources/text", status_code=201)
async def create_source(request: Request, body: TextSourceInput):
    require_session(request, write=True)
    if not body.label.strip() or not body.content.strip():
        raise HTTPException(422, "Label and content are required")
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return await add_text_source(db, identity, body.label, body.content)


@router.post("/knowledge", status_code=201)
async def create_fact(request: Request, body: FactInput):
    require_session(request, write=True)
    if not body.claim.strip():
        raise HTTPException(422, "Fact text is required")
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return await add_manual_fact(db, identity, body.type, body.claim,
                                     body.experience_context, body.publication_permission)


@router.patch("/knowledge/{fact_id}")
async def patch_fact(request: Request, fact_id: str, body: FactUpdate):
    require_session(request, write=True)
    if not body.claim.strip():
        raise HTTPException(422, "Fact text is required")
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            return await update_fact(db, identity, fact_id, body.expected_revision,
                                     body.claim, body.experience_context,
                                     body.publication_permission, body.status)
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/knowledge/{fact_id}")
async def remove_fact(request: Request, fact_id: str, body: FactDelete):
    require_session(request, write=True)
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            return await delete_fact(db, identity, fact_id, body.expected_revision)
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/sources/{source_id}/suggestions")
async def extract_source(request: Request, source_id: str):
    require_session(request, write=True)
    settings, identity = context()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise HTTPException(503, "Gemini configuration is missing")
    async with bound_database(settings, identity) as db:
        source = await db.sources.find_one({"_id": source_id, "installation_id": identity,
                                            "kind": "linkedin_profile_text"})
        if not source:
            raise HTTPException(404, "Source was not found")
        if source.get("extraction_status") == "completed":
            return {"created": 0, "already_extracted": True}
        content = source["content"]
        source_hash = source["content_hash"]
    try:
        suggestions = await suggest_facts(settings.gemini_api_key, settings.gemini_model, content)
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from None
    async with bound_database(settings, identity) as db:
        try:
            return await save_suggestions(db, identity, source_id, source_hash, suggestions)
        except KnowledgeConflict as exc:
            raise HTTPException(409, str(exc)) from None
