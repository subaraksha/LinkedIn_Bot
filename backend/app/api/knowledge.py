"""Private local dashboard endpoints for owner supplied professional information."""

import hashlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.connections import bound_database, context
from app.api.security import require_session
from app.domain.owner_profile import OwnerProfileInput
from app.storage.owner_profile import (
    ProfileConflict, get_owner_profile, list_questions, respond_question, save_owner_profile,
)
from app.services.knowledge_extraction import ExtractionError, suggest_facts
from app.services.knowledge_export import ExportError, export_status, generate_export
from app.services.resume_import import ResumeImportError, parse_resume, save_upload
from app.storage.knowledge import (
    KnowledgeConflict, add_manual_fact, add_resume_source, add_text_source, delete_fact, list_knowledge, save_suggestions, update_fact,
)

router = APIRouter(prefix="/api/v1")


async def _refresh_full(db, identity, settings):
    try:
        await generate_export(db, identity, settings.app_data_dir, "full")
    except Exception:
        pass  # The committed profile remains saved; export status reports outdated.


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
        source = await add_text_source(db, identity, body.label, body.content)
        await _refresh_full(db, identity, settings)
        return source


@router.post("/sources/resume", status_code=201)
async def upload_resume(request: Request, kind: Literal["pdf", "docx", "txt"]):
    require_session(request, write=True)
    settings, identity = context()
    path = None
    retained = False
    try:
        path, relative = await save_upload(request, settings.app_data_dir)
        sections = await parse_resume(path, kind)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        async with bound_database(settings, identity) as db:
            source, retained = await add_resume_source(
                db, identity, f"resume_{kind}", digest, relative, sections)
            await _refresh_full(db, identity, settings)
        return source
    except ResumeImportError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        if path is not None and not retained:
            path.unlink(missing_ok=True)


@router.post("/knowledge", status_code=201)
async def create_fact(request: Request, body: FactInput):
    require_session(request, write=True)
    if not body.claim.strip():
        raise HTTPException(422, "Fact text is required")
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        fact = await add_manual_fact(db, identity, body.type, body.claim,
                                     body.experience_context, body.publication_permission)
        await _refresh_full(db, identity, settings)
        return fact


@router.patch("/knowledge/{fact_id}")
async def patch_fact(request: Request, fact_id: str, body: FactUpdate):
    require_session(request, write=True)
    if not body.claim.strip():
        raise HTTPException(422, "Fact text is required")
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            fact = await update_fact(db, identity, fact_id, body.expected_revision,
                                     body.claim, body.experience_context,
                                     body.publication_permission, body.status)
            await _refresh_full(db, identity, settings)
            return fact
    except KnowledgeConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/knowledge/{fact_id}")
async def remove_fact(request: Request, fact_id: str, body: FactDelete):
    require_session(request, write=True)
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            result = await delete_fact(db, identity, fact_id, body.expected_revision)
            await _refresh_full(db, identity, settings)
            return result
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
                                            "kind": {"$in": ["linkedin_profile_text", "resume_pdf", "resume_docx", "resume_txt"]}})
        if not source:
            raise HTTPException(404, "Source was not found")
        if source.get("extraction_status") == "completed":
            return {"created": 0, "already_extracted": True}
        content = source["content"]
        if len(content) > 100_000:
            raise HTTPException(422, "Resume text is too long for one suggestion pass; use a shorter text source")
        source_hash = source["content_hash"]
    try:
        suggestions = await suggest_facts(settings.gemini_api_key, settings.gemini_model, content)
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from None
    async with bound_database(settings, identity) as db:
        try:
            result = await save_suggestions(db, identity, source_id, source_hash, suggestions)
            if not result["already_extracted"]:
                await _refresh_full(db, identity, settings)
            return result
        except KnowledgeConflict as exc:
            raise HTTPException(409, str(exc)) from None


class ExportRequest(BaseModel):
    scope: Literal["full", "public"]
    include_originals: bool = False


@router.get("/knowledge/export/status")
async def knowledge_export_status(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return await export_status(db, identity, settings.app_data_dir)


@router.post("/knowledge/exports")
async def download_knowledge_export(request: Request, body: ExportRequest):
    require_session(request, write=True)
    if body.scope == "public" and body.include_originals:
        raise HTTPException(422, "Originals cannot be included in a public profile")
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            for _ in range(3):
                bundle, revision = await generate_export(
                    db, identity, settings.app_data_dir, body.scope,
                    include_originals=body.include_originals)
                owner = await db.owner_settings.find_one({"_id": "owner", "installation_id": identity})
                if owner and owner.get("profile_revision", 0) == revision:
                    break
            else:
                raise ExportError("Profile changed during export; retry")
    except Exception:
        raise HTTPException(503, "Knowledge export could not be generated; your saved facts are unchanged") from None
    name = "full-knowledge.zip" if body.scope == "full" else "public-profile.zip"
    return Response(bundle, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Profile-Revision": str(revision),
    })


class OwnerProfileUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    data: OwnerProfileInput


class QuestionResponse(BaseModel):
    expected_revision: int = Field(ge=0)
    answer: str | None = Field(default=None, max_length=2000)
    skip: bool = False


@router.get("/owner/profile")
async def read_owner_profile(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return await get_owner_profile(db, identity)


@router.patch("/owner/profile")
async def patch_owner_profile(request: Request, body: OwnerProfileUpdate):
    require_session(request, write=True)
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            profile = await save_owner_profile(db, identity, body.expected_revision, body.data)
            await _refresh_full(db, identity, settings)
            return profile
    except ProfileConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/clarifications")
async def read_questions(request: Request):
    require_session(request)
    settings, identity = context()
    async with bound_database(settings, identity) as db:
        return {"questions": await list_questions(db, identity)}


@router.post("/clarifications/{question_id}/response")
async def answer_question(request: Request, question_id: str, body: QuestionResponse):
    require_session(request, write=True)
    if not body.skip and not (body.answer or "").strip():
        raise HTTPException(422, "Enter an answer or choose Skip")
    settings, identity = context()
    try:
        async with bound_database(settings, identity) as db:
            result = await respond_question(db, identity, question_id, body.expected_revision,
                                            body.answer, body.skip)
            await _refresh_full(db, identity, settings)
            return result
    except ProfileConflict as exc:
        raise HTTPException(409, str(exc)) from None
