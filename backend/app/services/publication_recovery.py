"""Explicit reconciliation for an uncertain one-shot LinkedIn publication."""

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import psutil
from pymongo.errors import DuplicateKeyError

from app.storage.process_lock import WorkerAlreadyRunning, WorkerLock
from app.storage.publication_journal import load_success


class RecoveryError(ValueError):
    pass


POST_URL = re.compile(r"\Ahttps://www\.linkedin\.com/feed/update/(urn:li:(?:share|ugcPost):[0-9]+)/?\Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validated_post_url(value: str) -> tuple[str, str]:
    match = POST_URL.fullmatch(value.strip())
    if not match:
        raise RecoveryError("Use the exact LinkedIn post URL from your profile")
    return value.strip().rstrip("/"), match.group(1)


async def recovery_status(db, workflow_id: str) -> dict:
    workflow = await db.workflows.find_one({"_id": workflow_id})
    if not workflow or workflow.get("state") != "PUBLISH_UNKNOWN":
        raise RecoveryError("This workflow has no uncertain publication")
    attempt = await db.publication_attempts.find_one({"workflow_id": workflow_id,
        "status": "unknown"})
    if not attempt:
        raise RecoveryError("The uncertain attempt is unavailable")
    control = await db.runtime_control.find_one({"_id": "publication"})
    return {"attempt_id": attempt["_id"], "workflow_revision": workflow["revision"],
            "result_reason": attempt.get("result_reason"),
            "send_started_at": attempt.get("send_started_at"),
            "quiesced": bool(control and control.get("blocked") and
                control.get("attempt_id") == attempt["_id"] and control.get("quiesced_at")),
            "post_id": attempt.get("post_id")}


async def quiesce_attempt(db, data_dir: Path, attempt_id: str) -> dict:
    """Launcher command: block sends, stop exact worker, verify OS lock, record proof."""
    attempt = await db.publication_attempts.find_one({"_id": attempt_id, "status": "unknown"})
    if not attempt:
        raise RecoveryError("No matching uncertain attempt")
    workflow = await db.workflows.find_one({"_id": attempt["workflow_id"],
        "state": "PUBLISH_UNKNOWN"})
    if not workflow:
        raise RecoveryError("The workflow no longer has an uncertain outcome")
    pid = attempt.get("worker_pid")
    created_at = attempt.get("worker_created_at")
    if not isinstance(pid, int) or not isinstance(created_at, (float, int)):
        raise RecoveryError("Publisher process identity is unavailable; recovery remains blocked")
    existing_control = await db.runtime_control.find_one({"_id": "publication"})
    if (existing_control and existing_control.get("blocked") and
            existing_control.get("attempt_id") not in (None, attempt_id)):
        raise RecoveryError("Another publication recovery is already blocked")
    if (existing_control and existing_control.get("blocked") and
            existing_control.get("attempt_id") == attempt_id and
            existing_control.get("quiesced_at")):
        return {"attempt_id": attempt_id, "quiesced": True}
    generation = secrets.token_hex(16)
    now = _now()
    await db.runtime_control.update_one({"_id": "publication"},
        {"$set": {"blocked": True, "attempt_id": attempt_id,
                  "recovery_generation": generation, "blocked_at": now},
         "$unset": {"quiesced_at": ""}}, upsert=True)
    receiver = await db.telegram_receivers.find_one({"_id":
        (workflow.get("pending_preview") or {}).get("bot_id"),
        "installation_id": workflow.get("installation_id")})
    identities = {(pid, float(created_at))}
    if receiver and isinstance(receiver.get("worker_pid"), int) and isinstance(
            receiver.get("worker_created_at"), (float, int)):
        identities.add((receiver["worker_pid"], float(receiver["worker_created_at"])))
    for process_id, process_created_at in identities:
        try:
            process = psutil.Process(process_id)
            if abs(process.create_time() - process_created_at) < 0.01:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            raise RecoveryError("Could not prove the old publisher stopped") from exc
    try:
        with WorkerLock(data_dir / "worker.lock"):
            if receiver:
                await db.telegram_receivers.update_one({"_id": receiver["_id"],
                    "installation_id": workflow.get("installation_id")},
                    {"$set": {"lease_until": _now()}})
            changed = await db.runtime_control.update_one({"_id": "publication",
                "blocked": True, "attempt_id": attempt_id,
                "recovery_generation": generation},
                {"$set": {"quiesced_at": _now(),
                          "quiesced_worker_pid": pid,
                          "quiesced_worker_created_at": created_at}})
            if changed.modified_count != 1:
                raise RecoveryError("Recovery block changed during quiescence")
    except WorkerAlreadyRunning as exc:
        raise RecoveryError("Another worker still holds the process lock") from exc
    return {"attempt_id": attempt_id, "quiesced": True}


async def resolve_outcome(db, data_dir: Path, installation_id: str, *,
                          workflow_id: str, attempt_id: str, expected_revision: int,
                          idempotency_key: str, outcome: str,
                          acknowledgement: bool, post_url: str | None) -> dict:
    if outcome not in {"published", "not_published"}:
        raise RecoveryError("Choose published or not_published")
    if not 8 <= len(idempotency_key) <= 120:
        raise RecoveryError("Invalid resolution request key")
    payload = {"workflow_id": workflow_id, "attempt_id": attempt_id,
               "expected_revision": expected_revision, "outcome": outcome,
               "acknowledgement": acknowledgement, "post_url": post_url}
    existing = await db.publication_resolutions.find_one({"idempotency_key": idempotency_key})
    if existing:
        if existing.get("request") != payload:
            raise RecoveryError("Resolution key belongs to a different request")
        return existing["result"]
    if not acknowledgement:
        raise RecoveryError("Confirm that you inspected the outcome and understand uncertainty")
    journal_id = load_success(data_dir, attempt_id)
    evidence = ("provider_response" if journal_id else
                "owner_reported" if outcome == "published" else
                "owner_reported_not_published")
    if outcome == "not_published" and journal_id:
        raise RecoveryError("A saved LinkedIn success response contradicts non-publication")
    if outcome == "published":
        if journal_id:
            from app.integrations.linkedin_posts import post_permalink
            final_url, post_id = post_permalink(journal_id), journal_id
        elif post_url:
            final_url, post_id = validated_post_url(post_url)
        else:
            raise RecoveryError("Provide the LinkedIn post URL you found")
    else:
        if post_url:
            raise RecoveryError("A post URL cannot accompany not_published")
        final_url, post_id = None, None
    now = _now()
    result = {"outcome": outcome, "post_url": final_url, "evidence_source": evidence,
              "workflow_id": workflow_id}
    try:
        async with db.client.start_session() as session:
            async with await session.start_transaction():
                workflow = await db.workflows.find_one({"_id": workflow_id,
                    "installation_id": installation_id, "state": "PUBLISH_UNKNOWN",
                    "revision": expected_revision}, session=session)
                attempt = await db.publication_attempts.find_one({"_id": attempt_id,
                    "workflow_id": workflow_id, "status": "unknown"}, session=session)
                control = await db.runtime_control.find_one({"_id": "publication",
                    "blocked": True, "attempt_id": attempt_id,
                    "quiesced_at": {"$exists": True}}, session=session)
                if not workflow or not attempt or not control:
                    raise RecoveryError("Recovery state changed; quiesce and refresh before resolving")
                changed = await db.workflows.update_one({"_id": workflow_id,
                    "state": "PUBLISH_UNKNOWN", "revision": expected_revision},
                    {"$set": {"state": "PUBLISHED" if outcome == "published" else "AWAITING_REVIEW",
                              "active": outcome != "published", "post_id": post_id,
                              "post_url": final_url, "post_evidence_source": evidence if final_url else None,
                              "pending_preview.status": "resolved_old_attempt",
                              "updated_at": now}, "$inc": {"revision": 1}}, session=session)
                if changed.modified_count != 1:
                    raise RecoveryError("Workflow changed while resolving")
                attempt_change = await db.publication_attempts.update_one({"_id": attempt_id,
                    "status": "unknown"}, {"$set": {"status": "resolved_" + outcome,
                    "post_id": post_id, "evidence_source": evidence,
                    "resolved_at": now}}, session=session)
                if attempt_change.modified_count != 1:
                    raise RecoveryError("Attempt changed while resolving")
                await db.jobs.update_many({"approval_id": attempt["approval_id"],
                    "kind": "publish"}, {"$set": {"status": "resolved_" + outcome,
                    "updated_at": now}}, session=session)
                await db.approval_receipts.update_one({"_id": attempt["approval_id"]},
                    {"$set": {"status": "resolved_" + outcome}}, session=session)
                await db.publication_resolutions.insert_one({"_id": idempotency_key,
                    "idempotency_key": idempotency_key, "attempt_id": attempt_id,
                    "workflow_id": workflow_id, "request": payload, "result": result,
                    "evidence_source": evidence, "created_at": now}, session=session)
                cleared = await db.runtime_control.update_one({"_id": "publication",
                    "attempt_id": attempt_id,
                    "recovery_generation": control["recovery_generation"]},
                    {"$set": {"blocked": False, "resolved_at": now},
                     "$unset": {"attempt_id": "", "quiesced_at": ""}}, session=session)
                if cleared.modified_count != 1:
                    raise RecoveryError("Recovery block changed while resolving")
                pending = workflow.get("pending_preview") or {}
                notice = (f"Publication recorded as published: {final_url}" if outcome == "published" else
                    "The uncertain attempt was marked not published after your inspection. "
                    "No retry was sent. Reply FINAL for a new preview and approval if you still want to publish.")
                await db.messages.insert_one({"_id": f"publication-resolution:{attempt_id}",
                    "channel": "telegram", "direction": "outbound",
                    "outbound_key": f"publication-notice:resolution:{attempt_id}",
                    "bot_id": pending.get("bot_id"), "chat_id": pending.get("chat_id"),
                    "binding_revision": pending.get("binding_revision"),
                    "text": notice, "status": "pending", "created_at": now}, session=session)
    except DuplicateKeyError:
        existing = await db.publication_resolutions.find_one({"idempotency_key": idempotency_key})
        if existing and existing.get("request") == payload:
            return existing["result"]
        raise RecoveryError("Resolution conflict; refresh and try again") from None
    return result
