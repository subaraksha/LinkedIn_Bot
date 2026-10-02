"""Local owner-reviewed Telegram pairing prototype."""

import argparse
import asyncio
import secrets
from urllib.parse import quote

from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import get_settings
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.telegram_pairing import (
    PairingError, begin_pairing, claim_receiver, confirm_candidate, mongo_client,
    pending_candidate, record_update, release_receiver,
)
from app.storage.installation import InstallationError, installation_identity


def context():
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database or not settings.telegram_bot_token:
        raise PairingError("MongoDB and Telegram bot configuration are required")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    return settings, installation_id


async def start() -> None:
    settings, installation_id = context()
    telegram = TelegramClient(settings.telegram_bot_token)
    mongo = mongo_client(settings.mongodb_uri)
    lease_token = secrets.token_urlsafe(24)
    bot_id = None
    try:
        identity = await telegram.identity()
        bot_id = identity["id"]
        if not identity["username"]:
            raise PairingError("Telegram bot has no username for a pairing link")
        webhook = await telegram.webhook_status()
        if webhook["configured"]:
            raise PairingError("Telegram webhook is configured; polling is blocked")
        db = mongo[settings.mongodb_database]
        challenge = secrets.token_urlsafe(16)
        await begin_pairing(db, installation_id, identity["id"], challenge)
        print(f"Bot: @{identity['username']} (ID suffix {identity['id'][-4:]})", flush=True)
        print("Open this one-use bot link on your Telegram account:", flush=True)
        print(f"https://t.me/{quote(identity['username'])}?start={challenge}", flush=True)
        print("Waiting up to ten minutes for your /start message. The account will remain unpaired until you confirm it.", flush=True)
        deadline = asyncio.get_running_loop().time() + 600
        while asyncio.get_running_loop().time() < deadline:
            offset = await claim_receiver(db, installation_id, identity["id"], lease_token)
            updates = await telegram.poll(offset, timeout=10)
            for update in updates:
                try:
                    await record_update(db, installation_id, identity["id"], lease_token, update)
                except DuplicateKeyError:
                    continue
                candidate = await pending_candidate(db, installation_id, identity["id"])
                if candidate:
                    print("Pairing candidate received:", flush=True)
                    print(f"Name: {candidate.get('first_name') or '(none)'}", flush=True)
                    print(f"Username: @{candidate.get('username') or '(none)'}", flush=True)
                    print(f"Numeric user ID: {candidate['sender_id']}", flush=True)
                    print("Ask the owner to verify this Telegram account before confirmation.", flush=True)
                    return
        print("Pairing challenge expired; run the command again.", flush=True)
    finally:
        if bot_id:
            await release_receiver(mongo[settings.mongodb_database], bot_id, lease_token)
        await telegram.close()
        await mongo.close()


async def show_status() -> None:
    settings, installation_id = context()
    telegram = TelegramClient(settings.telegram_bot_token)
    mongo = mongo_client(settings.mongodb_uri)
    try:
        identity = await telegram.identity()
        db = mongo[settings.mongodb_database]
        connection = await db.connections.find_one({"_id": "telegram"})
        if connection and (connection.get("installation_id") != installation_id or
                           connection.get("bot_id") != identity["id"]):
            raise PairingError("Stored Telegram bot or installation differs from configuration")
        candidate = await pending_candidate(db, installation_id, identity["id"])
        print(f"Telegram status: {(connection or {}).get('status', 'not_paired')}")
        print(f"Bot: @{identity['username']} (ID suffix {identity['id'][-4:]})")
        if candidate:
            print(f"Candidate: {candidate.get('first_name') or '(none)'} "
                  f"(@{candidate.get('username') or '(none)'}, ID {candidate['sender_id']})")
        if connection and connection.get("status") == "connected":
            print(f"Paired owner ID suffix: {str(connection.get('sender_id', ''))[-4:]}")
    finally:
        await telegram.close()
        await mongo.close()


async def confirm(sender_id: str) -> None:
    settings, installation_id = context()
    telegram = TelegramClient(settings.telegram_bot_token)
    mongo = mongo_client(settings.mongodb_uri)
    try:
        identity = await telegram.identity()
        candidate = await confirm_candidate(
            mongo[settings.mongodb_database], installation_id, identity["id"], sender_id
        )
        print(f"Telegram paired with numeric owner ID suffix {candidate['sender_id'][-4:]}")
    finally:
        await telegram.close()
        await mongo.close()


def run() -> None:
    parser = argparse.ArgumentParser(description="Pair one Telegram account to this local installation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("status")
    confirm_parser = sub.add_parser("confirm")
    confirm_parser.add_argument("sender_id", help="Exact numeric Telegram user ID displayed for the candidate")
    args = parser.parse_args()
    try:
        if args.command == "start":
            asyncio.run(start())
        elif args.command == "status":
            asyncio.run(show_status())
        else:
            asyncio.run(confirm(args.sender_id))
    except (PairingError, TelegramError, InstallationError, PyMongoError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    run()
