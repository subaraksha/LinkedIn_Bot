"""Initialize the configured owner's database after reviewing the destination."""

import argparse
import asyncio

from pymongo.errors import PyMongoError

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.storage.foundation import FoundationError, ensure_foundation
from app.storage.installation import InstallationError, installation_identity


async def apply() -> None:
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database:
        raise FoundationError("MONGODB_URI and MONGODB_DATABASE are required")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    client = mongo_client(settings.mongodb_uri)
    try:
        await ensure_foundation(client[settings.mongodb_database], installation_id)
    finally:
        await client.close()
    print("Owner database foundation is ready", flush=True)


def run() -> None:
    parser = argparse.ArgumentParser(description="Initialize the bound owner database and indexes")
    parser.add_argument("--apply", action="store_true", help="Write setup records and indexes to the configured database")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("Run with --apply to initialize the configured owner database")
    try:
        asyncio.run(apply())
    except PyMongoError:
        raise SystemExit("Foundation setup failed: MongoDB is unavailable or rejected an index") from None
    except (FoundationError, InstallationError) as exc:
        raise SystemExit(f"Foundation setup failed: {exc}") from None


if __name__ == "__main__":
    run()
