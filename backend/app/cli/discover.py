"""Standalone trend collector for installations without a Telegram receiver."""
import asyncio
import secrets
from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.storage.installation import installation_identity
from app.storage.process_lock import WorkerLock
from app.services.trend_discovery import discovery_loop


async def collect():
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database:
        raise SystemExit('MongoDB configuration is required')
    identity = installation_identity(settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database)
    client = mongo_client(settings.mongodb_uri)
    try:
        db = client[settings.mongodb_database]
        owner = await db.owner_settings.find_one({'_id': 'owner', 'installation_id': identity})
        if not owner:
            raise SystemExit('Run database foundation setup before discovery')
        await discovery_loop(db, identity, secrets.token_urlsafe(24))
    finally:
        await client.close()


def run():
    settings = get_settings()
    try:
        with WorkerLock(settings.app_data_dir / 'discovery.lock'):
            asyncio.run(collect())
    except KeyboardInterrupt:
        pass
