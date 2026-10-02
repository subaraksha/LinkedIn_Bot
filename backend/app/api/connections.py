"""Dashboard owned LinkedIn and Telegram setup flows."""

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.api.security import require_session, session_digest
from app.config import get_settings
from app.integrations.linkedin import LinkedInError, exchange_code_details, member_identity
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.telegram_pairing import (
    PairingError, begin_pairing, claim_receiver, confirm_candidate,
    mongo_client, record_update, release_receiver,
)
from app.storage.installation import InstallationError, installation_identity
from app.storage.linkedin_connection import ConnectionError, save_connection


router = APIRouter(prefix="/api")
CALLBACK_PATH = "/api/v1/connections/linkedin/callback"
pairing_task: asyncio.Task | None = None
pairing_error: str | None = None
oauth_states: dict[str, tuple[str, float]] = {}


def context():
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database:
        raise HTTPException(status_code=503, detail="MongoDB configuration is missing")
    try:
        identity = installation_identity(
            settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
        )
    except InstallationError:
        raise HTTPException(status_code=503, detail="Installation binding needs attention") from None
    return settings, identity


@asynccontextmanager
async def bound_database(settings, installation_id):
    client = mongo_client(settings.mongodb_uri)
    try:
        db = client[settings.mongodb_database]
        owner = await db.owner_settings.find_one({"_id": "owner"})
        if not owner or owner.get("installation_id") != installation_id:
            raise HTTPException(status_code=503, detail="Owner database binding needs attention")
        yield db
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable") from None
    finally:
        await client.close()


async def poll_pairing(settings, installation_id: str, bot_id: str) -> None:
    global pairing_error
    client = mongo_client(settings.mongodb_uri)
    telegram = TelegramClient(settings.telegram_bot_token)
    lease_token = secrets.token_urlsafe(24)
    try:
        db = client[settings.mongodb_database]
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            pairing = await db.telegram_pairings.find_one({"_id": bot_id})
            if pairing and pairing.get("status") == "candidate":
                return
            offset = await claim_receiver(db, installation_id, bot_id, lease_token)
            for update in await telegram.poll(offset, timeout=10):
                try:
                    await record_update(db, installation_id, bot_id, lease_token, update)
                except DuplicateKeyError:
                    pass
                pairing = await db.telegram_pairings.find_one({"_id": bot_id})
                if pairing and pairing.get("status") == "candidate":
                    return
    except (PairingError, TelegramError, PyMongoError):
        pairing_error = "Telegram pairing receiver needs attention"
    finally:
        try:
            await release_receiver(client[settings.mongodb_database], bot_id, lease_token)
        except PyMongoError:
            pass
        await telegram.close()
        await client.close()


@router.post("/connections/telegram/pairing/start")
async def start_pairing(request: Request) -> dict:
    global pairing_task, pairing_error
    session = require_session(request, write=True)
    settings, installation_id = context()
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="Telegram bot token is missing")
    if pairing_task and not pairing_task.done():
        raise HTTPException(status_code=409, detail="Pairing is already in progress")
    telegram = TelegramClient(settings.telegram_bot_token)
    try:
        identity = await telegram.identity()
        webhook = await telegram.webhook_status()
        if not identity.get("username") or webhook["configured"]:
            raise HTTPException(status_code=409, detail="Bot needs a username and no active webhook")
        challenge = secrets.token_urlsafe(16)
        async with bound_database(settings, installation_id) as db:
            await begin_pairing(db, installation_id, identity["id"], challenge,
                                session_digest=session_digest(session.id))
        pairing_error = None
        pairing_task = asyncio.create_task(poll_pairing(settings, installation_id, identity["id"]))
        return {"url": f"https://t.me/{identity['username']}?start={challenge}",
                "bot_username": identity["username"]}
    except (PairingError, TelegramError):
        raise HTTPException(status_code=409, detail="Telegram pairing could not start") from None
    finally:
        await telegram.close()


@router.get("/connections/telegram/pairing")
async def pairing_status(request: Request) -> dict:
    session = require_session(request)
    settings, installation_id = context()
    async with bound_database(settings, installation_id) as db:
        pairing = await db.telegram_pairings.find_one({
            "session_digest": session_digest(session.id), "installation_id": installation_id
        })
    if not pairing:
        return {"status": "none", "error": pairing_error}
    expiry = pairing["expires_at"].replace(tzinfo=timezone.utc)
    status = pairing.get("status", "none") if expiry > datetime.now(timezone.utc) else "expired"
    candidate = pairing.get("candidate") if status == "candidate" else None
    return {"status": status, "candidate": candidate, "error": pairing_error}


class PairingConfirmation(BaseModel):
    sender_id: str


@router.post("/connections/telegram/pairing/confirm")
async def confirm_pairing(request: Request, body: PairingConfirmation) -> dict:
    session = require_session(request, write=True)
    settings, installation_id = context()
    try:
        async with bound_database(settings, installation_id) as db:
            pairing = await db.telegram_pairings.find_one({
                "session_digest": session_digest(session.id), "installation_id": installation_id,
                "status": "candidate"
            })
            if not pairing:
                raise HTTPException(status_code=409, detail="No pairing candidate for this session")
            candidate = await confirm_candidate(
                db, installation_id, pairing["_id"], body.sender_id,
                session_digest=session_digest(session.id),
            )
        return {"status": "paired", "owner_id_suffix": candidate["sender_id"][-4:]}
    except PairingError:
        raise HTTPException(status_code=409, detail="Pairing candidate expired or changed") from None


@router.post("/connections/linkedin/start")
async def start_linkedin(request: Request) -> dict:
    session = require_session(request, write=True)
    settings, installation_id = context()
    redirect = settings.linkedin_redirect_uri or ""
    parsed = urlparse(redirect)
    if (parsed.scheme != "https" or parsed.hostname != settings.app_host
            or parsed.port != settings.app_port or parsed.path != CALLBACK_PATH
            or parsed.query or parsed.fragment
            or not settings.linkedin_client_id or not settings.linkedin_client_secret):
        raise HTTPException(status_code=503, detail="LinkedIn callback configuration needs attention")
    async with bound_database(settings, installation_id):
        pass
    state = secrets.token_urlsafe(32)
    oauth_states[state] = (session.id, time.monotonic() + 600)
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code", "client_id": settings.linkedin_client_id,
        "redirect_uri": redirect, "state": state,
        "scope": "openid profile w_member_social",
    })
    return {"url": url}


@router.get("/v1/connections/linkedin/callback")
async def linkedin_callback(request: Request):
    session = require_session(request)
    state = request.query_params.get("state", "")
    pending = oauth_states.pop(state, None)
    if not pending or pending[0] != session.id or time.monotonic() >= pending[1]:
        raise HTTPException(status_code=400, detail="LinkedIn sign-in expired or changed")
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="LinkedIn did not grant access")
    settings, installation_id = context()
    try:
        token = await exchange_code_details(
            settings.linkedin_client_id, settings.linkedin_client_secret,
            settings.linkedin_redirect_uri, code,
        )
        member = await member_identity(token.access_token)
        await save_connection(settings.mongodb_uri, settings.mongodb_database,
                              installation_id, member, token)
    except (LinkedInError, ConnectionError):
        raise HTTPException(status_code=502, detail="LinkedIn connection failed") from None
    return RedirectResponse("/?linkedin=connected", status_code=303)


async def shutdown() -> None:
    global pairing_task
    if pairing_task and not pairing_task.done():
        pairing_task.cancel()
        try:
            await pairing_task
        except asyncio.CancelledError:
            pass
    pairing_task = None
