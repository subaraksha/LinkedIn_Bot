"""Local launcher operation that proves an uncertain publisher has stopped."""

import argparse
import asyncio

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.services.publication_recovery import RecoveryError, quiesce_attempt
from app.storage.installation import installation_identity


async def _run(attempt_id: str) -> None:
    settings = get_settings()
    installation_identity(settings.app_data_dir, settings.mongodb_uri,
                          settings.mongodb_database)
    mongo = mongo_client(settings.mongodb_uri)
    try:
        result = await quiesce_attempt(mongo[settings.mongodb_database],
                                       settings.app_data_dir, attempt_id)
        print(f"Publisher stopped and recovery blocked for {result['attempt_id']}")
    finally:
        await mongo.close()


def run() -> None:
    parser = argparse.ArgumentParser(description="Stop and verify the old publisher before resolving an uncertain LinkedIn post")
    parser.add_argument("--attempt", required=True, help="Attempt ID shown in the local dashboard")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.attempt))
    except RecoveryError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    run()
