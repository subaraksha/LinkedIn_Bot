import asyncio
import secrets

from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import get_settings
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.linkedin_posts import PostContractError
from app.integrations.telegram_pairing import (
    PairingError, claim_receiver, mark_receiver_restart, mongo_client,
    record_owner_update, release_receiver,
)
from app.services.approval_intake import drain_owner_messages
from app.services.draft_workflow import dispatch_draft_previews, run_draft_job
from app.services.topic_workflow import (
    dispatch_topic_messages, reminder_tick, run_research_job, schedule_tick,
)
from app.services.publication import (
    PublicationError, dispatch_publication_notice, publish_one, recover_inflight,
)
from app.storage.installation import InstallationError, installation_identity
from app.storage.process_lock import WorkerAlreadyRunning, WorkerLock


async def receive() -> None:
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database or not settings.telegram_bot_token:
        raise PairingError("MongoDB and Telegram bot configuration are required")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    telegram = TelegramClient(settings.telegram_bot_token)
    mongo = mongo_client(settings.mongodb_uri)
    lease_token = secrets.token_urlsafe(24)
    bot_id = None
    research_task = None
    draft_task = None
    try:
        identity = await telegram.identity()
        bot_id = identity["id"]
        if (await telegram.webhook_status())["configured"]:
            raise PairingError("Telegram webhook is configured; polling is blocked")
        db = mongo[settings.mongodb_database]
        connection = await db.connections.find_one({"_id": "telegram"})
        if (not connection or connection.get("installation_id") != installation_id
                or connection.get("bot_id") != bot_id or connection.get("status") != "connected"):
            raise PairingError("Telegram bot is not paired to this installation")
        await claim_receiver(db, installation_id, bot_id, lease_token, lease_seconds=90)
        await mark_receiver_restart(db, installation_id, bot_id)
        await recover_inflight(db, settings.app_data_dir)
        await drain_owner_messages(db, installation_id, publishing_enabled=False)
        print(
            f"Telegram receiver active for bot ID suffix {bot_id[-4:]}; "
            f"publishing {'enabled' if settings.publishing_enabled else 'disabled'}",
            flush=True,
        )
        poll_failures = 0
        while True:
            if research_task and research_task.done():
                try:
                    research_task.result()
                except Exception:
                    print("Topic research needs attention; the job can be retried after its lease expires", flush=True)
                research_task = None
            if draft_task and draft_task.done():
                try:
                    draft_task.result()
                except Exception:
                    print("Draft generation needs attention", flush=True)
                draft_task = None
            await schedule_tick(db, installation_id)
            await reminder_tick(db, installation_id)
            if research_task is None and settings.gemini_api_key and settings.gemini_model:
                research_task = asyncio.create_task(run_research_job(
                    db, installation_id, settings.gemini_api_key, settings.gemini_model,
                    lease_token))
            if draft_task is None and settings.gemini_api_key and settings.gemini_model:
                draft_task = asyncio.create_task(run_draft_job(
                    db, installation_id, settings.gemini_api_key, settings.gemini_model,
                    lease_token))
            offset = await claim_receiver(db, installation_id, bot_id, lease_token, lease_seconds=90)
            try:
                updates = await telegram.poll(offset, timeout=30)
            except TelegramError:
                if poll_failures == 0:
                    await mark_receiver_restart(db, installation_id, bot_id)
                    print("Telegram polling interrupted; retrying while saved research continues", flush=True)
                poll_failures += 1
                await asyncio.sleep(min(30, 2 ** min(poll_failures, 5)))
                continue
            poll_failures = 0
            counts = {"owner_input": 0, "rejected": 0}
            for update in updates:
                try:
                    disposition = await record_owner_update(
                        db, installation_id, bot_id, lease_token, update
                    )
                except DuplicateKeyError:
                    continue
                counts[disposition] += 1
            if updates:
                await drain_owner_messages(
                    db, installation_id, publishing_enabled=settings.publishing_enabled
                )
                print(f"Saved {counts['owner_input']} owner message(s); rejected {counts['rejected']} update(s)", flush=True)
            await publish_one(db, settings, installation_id, bot_id, lease_token)
            await dispatch_publication_notice(db, telegram, bot_id)
            await dispatch_topic_messages(db, telegram, installation_id)
            await dispatch_draft_previews(db, telegram, installation_id)
    finally:
        if research_task and not research_task.done():
            research_task.cancel()
            try:
                await research_task
            except asyncio.CancelledError:
                pass
        if draft_task and not draft_task.done():
            draft_task.cancel()
            try:
                await draft_task
            except asyncio.CancelledError:
                pass
        if bot_id:
            try:
                await release_receiver(mongo[settings.mongodb_database], bot_id, lease_token)
            except PyMongoError:
                pass
        await telegram.close()
        await mongo.close()


def run() -> None:
    try:
        settings = get_settings()
        with WorkerLock(settings.app_data_dir / "worker.lock"):
            asyncio.run(receive())
    except KeyboardInterrupt:
        print("Telegram receiver stopped", flush=True)
    except (PairingError, TelegramError, PublicationError, InstallationError,
            WorkerAlreadyRunning, PostContractError, PyMongoError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    run()
