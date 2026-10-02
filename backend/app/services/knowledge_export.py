"""Versioned portable knowledge bundles built from a consistent owner snapshot."""

import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from app.domain.owner_profile import DEFAULT_PROFILE, OwnerProfileInput
from app.services.guided_questions import derive_questions, merge_questions
from app.services.draft_context import blocked_terms
from pymongo.read_concern import ReadConcern

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "domain" / "profile.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
FORMAT_VERSION = 1
MAX_DOCUMENTS = 10_000
MAX_ORIGINALS_BYTES = 100 * 1024 * 1024
_WRITE_LOCK = threading.Lock()


class ExportError(RuntimeError):
    pass


def _iso(value: datetime) -> str:
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _md(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+!|>])", r"\\\1", value).replace("\n", " ")


async def read_snapshot(db, installation_id: str) -> dict:
    async with db.client.start_session() as session:
        async with await session.start_transaction(read_concern=ReadConcern("snapshot")):
            owner = await db.owner_settings.find_one({"_id": "owner", "installation_id": installation_id}, session=session)
            if not owner:
                raise ExportError("Owner database binding needs attention")
            sources = await db.sources.find({"installation_id": installation_id}, session=session).limit(MAX_DOCUMENTS + 1).to_list(length=MAX_DOCUMENTS + 1)
            entries = await db.knowledge_entries.find({"installation_id": installation_id}, session=session).limit(MAX_DOCUMENTS + 1).to_list(length=MAX_DOCUMENTS + 1)
            profile_record = await db.owner_profiles.find_one({"_id": "profile", "installation_id": installation_id}, session=session)
            clarifications = await db.clarifications.find({"installation_id": installation_id}, session=session).limit(MAX_DOCUMENTS + 1).to_list(length=MAX_DOCUMENTS + 1)
            if len(sources) > MAX_DOCUMENTS or len(entries) > MAX_DOCUMENTS or len(clarifications) > MAX_DOCUMENTS:
                raise ExportError("Profile is too large for this export version")
            return {"revision": owner.get("profile_revision", 0), "updated_at": owner.get("updated_at"),
                    "sources": sources, "entries": entries,
                    "profile": profile_record["data"] if profile_record else DEFAULT_PROFILE.model_dump(),
                    "clarifications": clarifications}


def project_profile(snapshot: dict, scope: str, generated_at: datetime) -> dict:
    if scope not in {"full", "public"}:
        raise ExportError("Unsupported export scope")
    entries = snapshot["entries"]
    owner_profile = OwnerProfileInput.model_validate(snapshot.get("profile", DEFAULT_PROFILE.model_dump()))
    data = owner_profile.model_dump()
    if scope == "public":
        entries = [entry for entry in entries if entry.get("status") == "confirmed"
                   and entry.get("publication_permission") == "public"
                   and not blocked_terms(entry.get("claim", ""), data)
                   and not blocked_terms(entry.get("experience_context", ""), data)]
    else:
        entries = [entry for entry in entries if entry.get("status") != "deleted"]
    facts = []
    referenced = set()
    excerpts: dict[str, list[dict]] = {}
    for entry in entries:
        evidence = []
        if scope == "full":
            for item in entry.get("evidence", []):
                source_id = item.get("source_id")
                if not source_id:
                    raise ExportError("Fact has evidence without a source ID")
                reference = {"source_id": source_id, "kind": item.get("kind", "unknown")}
                if item.get("quote"):
                    reference["quote"] = item["quote"]
                    excerpts.setdefault(source_id, []).append({
                        "fact_id": entry["_id"], "quote": item["quote"],
                        "location": item.get("location"),
                    })
                if "location" in item:
                    reference["location"] = item["location"]
                evidence.append(reference)
                referenced.add(source_id)
        facts.append({"id": entry["_id"], "type": entry["type"], "claim": entry["claim"],
                      "status": entry["status"], "publication_permission": entry["publication_permission"],
                      "experience_context": entry.get("experience_context", ""),
                      "revision": entry["revision"], "updated_at": _iso(entry["updated_at"]),
                      "evidence": evidence})
    facts.sort(key=lambda item: item["id"])
    sources = []
    if scope == "full":
        for source in snapshot["sources"]:
            if source["kind"] == "owner_statement" and source["_id"] not in referenced:
                continue
            item = {"id": source["_id"], "kind": source["kind"],
                    "label": source.get("label", ""), "source_version": source.get("source_version", 1),
                    "excerpts": excerpts.get(source["_id"], [])}
            if source.get("content_hash"):
                item["content_hash"] = source["content_hash"]
            if source.get("parser_version"):
                item["parser_version"] = source["parser_version"]
            sources.append(item)
        sources.sort(key=lambda item: item["id"])
        missing = referenced - {source["id"] for source in sources}
        if missing:
            raise ExportError("Fact evidence refers to a missing source")
    markers = []
    if scope == "full":
        for entry in snapshot["entries"]:
            if entry.get("status") == "deleted" and entry.get("claim_hash"):
                marker = {"id": entry["_id"], "claim_hash": entry["claim_hash"]}
                if entry.get("source_extraction_id"):
                    marker["source_extraction_id"] = entry["source_extraction_id"]
                markers.append(marker)
        markers.sort(key=lambda item: item["id"])
    goals = ({key: data[key] for key in ("target_roles", "audience", "interests", "content_goals")}
             if scope == "full" else {})
    safe_style = ("tone", "length", "technical_depth", "use_emojis", "use_hashtags")
    preference_keys = safe_style + (("avoid_phrases", "avoid_styles", "avoid_topics",
                                     "confidential_details", "writing_samples") if scope == "full" else ())
    preferences = {key: data[key] for key in preference_keys}
    clarifications = []
    if scope == "full":
        current = derive_questions(data, [entry for entry in snapshot["entries"] if entry.get("status") != "deleted"])
        merged = merge_questions(current, snapshot.get("clarifications", []))
        active_ids = {item["id"] for item in merged}
        merged += [{"id": item["_id"], "kind": item["kind"], "prompt": item["prompt"],
                    "fact_id": item.get("fact_id"), "status": item["status"],
                    "answer": item.get("answer"), "revision": item["revision"]}
                   for item in snapshot.get("clarifications", []) if item["_id"] not in active_ids]
        clarifications = sorted(merged, key=lambda item: item["id"])
    profile = {"format_version": FORMAT_VERSION, "scope": scope,
               "profile_revision": snapshot["revision"], "generated_at": _iso(generated_at),
               "facts": facts, "sources": sources, "clarifications": clarifications,
               "goals": goals, "preferences": preferences, "suppression_markers": markers}
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(profile)
    return profile


def render_markdown(profile: dict) -> bytes:
    title = "Full knowledge (private)" if profile["scope"] == "full" else "Public profile"
    lines = [f"# {title}", "", f"Profile revision: {profile['profile_revision']}",
             f"Generated: {profile['generated_at']}", "", "## Facts", ""]
    if not profile["facts"]:
        lines += ["No current facts in this export.", ""]
    for fact in profile["facts"]:
        lines += [f"- **{_md(fact['claim'])}**", f"  - Type: {fact['type']}",
                  f"  - Status: {fact['status']}",
                  f"  - Publication permission: {fact['publication_permission']}",
                  f"  - Updated: {fact['updated_at']}"]
        if fact["experience_context"]:
            lines.append(f"  - Context: {_md(fact['experience_context'])}")
        for evidence in fact["evidence"]:
            quote = evidence.get("quote", "")
            suffix = f" ({_md(evidence['location'])})" if evidence.get("location") else ""
            lines.append(f"  - Evidence: source `{evidence['source_id']}`{suffix} — {_md(quote)}")
        lines.append("")
    if profile["scope"] == "full":
        lines += ["## Goals", ""]
        for key, value in profile["goals"].items():
            lines.append(f"- {key.replace('_', ' ').title()}: {_md(', '.join(value) if isinstance(value, list) else str(value))}")
        lines += ["", "## Writing preferences and boundaries", ""]
        for key, value in profile["preferences"].items():
            display = ', '.join(value) if isinstance(value, list) else str(value)
            lines.append(f"- {key.replace('_', ' ').title()}: {_md(display)}")
        lines += ["", "## Guided questions", ""]
        for question in profile["clarifications"]:
            lines.append(f"- {_md(question['prompt'])} [{question['status']}]")
            if question.get("answer"):
                lines.append(f"  - Answer: {_md(question['answer'])}")
        lines += ["", "## Sources", ""]
        for source in profile["sources"]:
            lines.append(f"- {_md(source['label'])} (`{source['id']}`, {source['kind']})")
        lines += ["", f"Suppression markers: {len(profile['suppression_markers'])}", ""]
    if profile["scope"] == "public":
        lines += ["## Writing style", ""]
        for key, value in profile["preferences"].items():
            lines.append(f"- {key.replace('_', ' ').title()}: {_md(str(value))}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _original_files(snapshot: dict, data_dir: Path) -> dict[str, tuple[bytes, str]]:
    root = (data_dir / "uploads").resolve()
    files = {}
    size = 0
    for source in snapshot["sources"]:
        if not source["kind"].startswith("resume_") or not source.get("local_relative_path"):
            continue
        source_id = str(UUID(source["_id"]))
        relative = Path(source["local_relative_path"])
        target = (data_dir / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ExportError("An uploaded original is missing or outside local storage")
        raw = target.read_bytes()
        size += len(raw)
        if size > MAX_ORIGINALS_BYTES:
            raise ExportError("Original files exceed the export size limit")
        if hashlib.sha256(raw).hexdigest() != source.get("content_hash"):
            raise ExportError("An uploaded original changed after import")
        extension = source["kind"].removeprefix("resume_")
        files[f"sources/{source_id}.{extension}"] = (raw, source_id)
    return files


def build_bundle(profile: dict, snapshot: dict, data_dir: Path, *, include_originals: bool = False) -> bytes:
    if include_originals and profile["scope"] != "full":
        raise ExportError("Originals are available only in a full export")
    files: dict[str, tuple[bytes, str | None]] = {
        "profile.json": (_json_bytes(profile), None),
        "profile.md": (render_markdown(profile), None),
        "profile.schema.json": (_json_bytes(SCHEMA), None),
    }
    if include_originals:
        files.update(_original_files(snapshot, data_dir))
    manifest = {"format_version": FORMAT_VERSION, "scope": profile["scope"],
                "profile_revision": profile["profile_revision"],
                "generated_at": profile["generated_at"], "schema_id": SCHEMA["$id"],
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                                 **({"source_id": source_id} if source_id else {})}
                          for name, (data, source_id) in sorted(files.items())}}
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, (data, _) in files.items():
                archive.writestr(name, data)
            archive.writestr("manifest.json", _json_bytes(manifest))
        bundle = output.getvalue()
    verify_bundle(bundle)
    return bundle


def verify_bundle(bundle: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ExportError("Export archive has invalid paths")
        manifest = json.loads(archive.read("manifest.json"))
        if set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise ExportError("Export manifest does not match files")
        for name, details in manifest["files"].items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise ExportError("Export contains an unsafe path")
            content = archive.read(name)
            if len(content) != details["bytes"] or hashlib.sha256(content).hexdigest() != details["sha256"]:
                raise ExportError("Export file checksum failed")
        profile = json.loads(archive.read("profile.json"))
        schema = json.loads(archive.read("profile.schema.json"))
        if schema.get("$id") != manifest.get("schema_id") or manifest.get("format_version") != FORMAT_VERSION:
            raise ExportError("Export schema version is unsupported")
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(profile)
        if profile["scope"] != manifest["scope"] or profile["profile_revision"] != manifest["profile_revision"]:
            raise ExportError("Export profile and manifest disagree")
        return manifest


def _stored_revision(path: Path) -> int | None:
    try:
        return verify_bundle(path.read_bytes())["profile_revision"]
    except Exception:
        return None


def _save_bundle(data_dir: Path, scope: str, bundle: bytes, revision: int) -> None:
    export_dir = data_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(export_dir, 0o700)
    target = export_dir / ("full-knowledge.zip" if scope == "full" else "public-profile.zip")
    with _WRITE_LOCK:
        if (existing := _stored_revision(target)) is not None and existing > revision:
            return
        handle, temp_name = tempfile.mkstemp(prefix=".export-", dir=export_dir)
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "wb") as output:
                output.write(bundle)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, target)
            directory = os.open(export_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temp_name).unlink(missing_ok=True)


async def generate_export(db, installation_id: str, data_dir: Path, scope: str,
                          *, include_originals: bool = False) -> tuple[bytes, int]:
    snapshot = await read_snapshot(db, installation_id)
    profile = project_profile(snapshot, scope, datetime.now(timezone.utc))
    bundle = await asyncio.to_thread(build_bundle, profile, snapshot, data_dir,
                                     include_originals=include_originals)
    if not include_originals:
        await asyncio.to_thread(_save_bundle, data_dir, scope, bundle, snapshot["revision"])
    return bundle, snapshot["revision"]


async def export_status(db, installation_id: str, data_dir: Path) -> dict:
    owner = await db.owner_settings.find_one({"_id": "owner", "installation_id": installation_id})
    if not owner:
        raise ExportError("Owner database binding needs attention")
    revision = owner.get("profile_revision", 0)
    full = await asyncio.to_thread(_stored_revision, data_dir / "exports" / "full-knowledge.zip")
    public = await asyncio.to_thread(_stored_revision, data_dir / "exports" / "public-profile.zip")
    return {"profile_revision": revision,
            "full_export_revision": full, "full_status": "current" if full == revision else "outdated",
            "public_export_revision": public, "public_status": "current" if public == revision else "outdated"}
