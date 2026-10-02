"""Send an explicitly non-publishing fixture preview to the paired Telegram chat."""

import asyncio

from pymongo.errors import PyMongoError

from app.config import get_settings
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.telegram_pairing import mongo_client
from app.services.preview_delivery import (
    PreviewDeliveryError, create_demo_preview, dispatch_demo_preview,
)
from app.storage.installation import InstallationError, installation_identity


async def send() -> None:
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database or not settings.telegram_bot_token:
        raise PreviewDeliveryError("MongoDB and Telegram configuration are required")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    mongo = mongo_client(settings.mongodb_uri)
    telegram = TelegramClient(settings.telegram_bot_token)
    try:
        identity = await telegram.identity()
        db = mongo[settings.mongodb_database]
        workflow_id = await create_demo_preview(db, installation_id, identity["id"])
        print("Dry-run preview saved; sending its three ordered Telegram messages", flush=True)
        await dispatch_demo_preview(db, workflow_id, telegram)
        print("Dry-run preview delivery accepted by Telegram; no approval challenge or LinkedIn post exists", flush=True)
    finally:
        await telegram.close()
        await mongo.close()


def run() -> None:
    try:
        asyncio.run(send())
    except (PreviewDeliveryError, TelegramError, InstallationError, PyMongoError) as exc:
        raise SystemExit(str(exc)) from None
