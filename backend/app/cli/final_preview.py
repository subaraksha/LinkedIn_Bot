"""Send a real final preview from an exact local UTF-8 file; never publish here."""

import argparse
import asyncio
from pathlib import Path

from pymongo.errors import PyMongoError

from app.config import get_settings
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.telegram_pairing import mongo_client
from app.services.final_preview import stage_final_preview
from app.services.preview_delivery import PreviewDeliveryError, dispatch_preview
from app.storage.installation import InstallationError, installation_identity
from app.storage.linkedin_connection import ConnectionError, connection_status


async def send(path: Path) -> None:
    settings = get_settings()
    if not settings.publishing_enabled:
        raise PreviewDeliveryError("PUBLISHING_ENABLED must be true before issuing a live approval command")
    if not all((settings.mongodb_uri, settings.mongodb_database,
                settings.telegram_bot_token, settings.linkedin_api_version)):
        raise PreviewDeliveryError("MongoDB, Telegram, and LinkedIn configuration are required")
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PreviewDeliveryError("Could not read UTF-8 post text file") from exc
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    status = await connection_status(
        settings.mongodb_uri, settings.mongodb_database, installation_id
    )
    if status["status"] != "connected":
        raise PreviewDeliveryError("LinkedIn connection needs reconnection")
    mongo = mongo_client(settings.mongodb_uri)
    telegram = TelegramClient(settings.telegram_bot_token)
    try:
        identity = await telegram.identity()
        db = mongo[settings.mongodb_database]
        workflow_id, code = await stage_final_preview(
            db, installation_id, identity["id"], body
        )
        print(f"Final preview {code} saved; delivering exact text to the paired Telegram chat", flush=True)
        await dispatch_preview(db, workflow_id, telegram, demo=False)
        print(f"Final preview {code} delivered and one-use command activated", flush=True)
    finally:
        await telegram.close()
        await mongo.close()


def run() -> None:
    parser = argparse.ArgumentParser(description="Preview one exact public LinkedIn post")
    parser.add_argument("post_file", type=Path, help="UTF-8 file containing the exact post body")
    parser.add_argument("--live", action="store_true", help="Acknowledge that Telegram approval will publish publicly")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Use --live only when ready to issue a real publication command")
    try:
        asyncio.run(send(args.post_file))
    except (PreviewDeliveryError, TelegramError, ConnectionError,
            InstallationError, PyMongoError) as exc:
        raise SystemExit(str(exc)) from None
