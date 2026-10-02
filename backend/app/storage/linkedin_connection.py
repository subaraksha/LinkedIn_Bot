"""LinkedIn token in OS credentials; metadata and owner binding in MongoDB."""

import uuid
from datetime import datetime, timedelta, timezone

import certifi
import keyring
from keyring.errors import KeyringError
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.integrations.linkedin import OAuthToken


class ConnectionError(RuntimeError):
    pass


def _service(installation_id: str) -> str:
    return f"linkedin-post-agent/{installation_id}"


def _validate_member(existing: dict | None, member_id: str) -> None:
    if existing and existing.get("member_id") != member_id:
        raise ConnectionError("Connected LinkedIn account differs; an explicit account-change flow is required")


def _connection_health(record: dict, token_present: bool, now: datetime) -> str:
    expiry = record.get("expires_at")
    expired = bool(expiry and expiry.replace(tzinfo=timezone.utc) <= now)
    return "reconnect_required" if expired or not token_present else "connected"


async def save_connection(
    mongo_uri: str,
    database: str,
    installation_id: str,
    member: dict,
    token: OAuthToken,
) -> None:
    client = AsyncMongoClient(
        mongo_uri, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where(), w="majority"
    )
    new_ref = f"linkedin-{uuid.uuid4()}"
    service = _service(installation_id)
    try:
        db = client[database]
        owner = await db.owner_settings.find_one({"_id": "owner"})
        if owner and owner.get("installation_id") != installation_id:
            raise ConnectionError("Database belongs to another installation")
        existing = await db.connections.find_one({"_id": "linkedin"})
        if existing and existing.get("installation_id") != installation_id:
            raise ConnectionError("LinkedIn connection belongs to another installation")
        _validate_member(existing, member["sub"])
        if not owner:
            try:
                await db.owner_settings.insert_one({"_id": "owner", "installation_id": installation_id})
            except DuplicateKeyError as exc:
                raise ConnectionError("Owner binding changed during connection") from exc
        try:
            keyring.set_password(service, new_ref, token.access_token)
        except KeyringError as exc:
            raise ConnectionError("OS credential store rejected the LinkedIn token") from exc
        now = datetime.now(timezone.utc)
        metadata = {
            "installation_id": installation_id,
            "status": "connected",
            "member_id": member["sub"],
            "member_name": member.get("name"),
            "secret_ref": new_ref,
            "expires_at": now + timedelta(seconds=token.expires_in) if token.expires_in else None,
            "granted_scopes": token.granted_scopes,
            "requested_scopes": "openid profile w_member_social",
            "connected_at": now,
        }
        try:
            async with client.start_session() as session:
                async with await session.start_transaction():
                    await db.connections.update_one(
                        {"_id": "linkedin", "installation_id": installation_id},
                        {"$set": metadata, "$inc": {"binding_revision": 1}},
                        upsert=True, session=session,
                    )
                    await db.workflows.update_many(
                        {"active": True,
                         "state": {"$in": ["AWAITING_APPROVAL", "PUBLISH_PENDING"]}},
                        {"$set": {"state": "AWAITING_REVIEW",
                                  "pending_preview.status": "invalidated_by_reconnect",
                                  "updated_at": now}, "$inc": {"revision": 1}},
                        session=session,
                    )
                    await db.jobs.update_many(
                        {"kind": "publish", "status": "pending"},
                        {"$set": {"status": "blocked_by_reconnect", "updated_at": now}},
                        session=session,
                    )
        except Exception:
            try:
                keyring.delete_password(service, new_ref)
            except KeyringError:
                pass
            raise
        if existing and existing.get("secret_ref"):
            try:
                keyring.delete_password(service, existing["secret_ref"])
            except KeyringError:
                pass  # Old token is orphaned, but the active reference is the new token.
    except PyMongoError as exc:
        raise ConnectionError("MongoDB could not save LinkedIn connection metadata") from exc
    finally:
        await client.close()


async def connection_status(mongo_uri: str, database: str, installation_id: str) -> dict:
    client = AsyncMongoClient(
        mongo_uri, serverSelectionTimeoutMS=8000, tlsCAFile=certifi.where(), w="majority"
    )
    try:
        owner = await client[database].owner_settings.find_one({"_id": "owner"})
        if owner and owner.get("installation_id") != installation_id:
            raise ConnectionError("Database belongs to another installation")
        record = await client[database].connections.find_one({"_id": "linkedin"})
        if not record:
            return {"status": "not_connected"}
        if record.get("installation_id") != installation_id:
            raise ConnectionError("LinkedIn connection belongs to another installation")
        expiry = record.get("expires_at")
        try:
            token_present = bool(keyring.get_password(_service(installation_id), record["secret_ref"]))
        except (KeyError, KeyringError):
            token_present = False
        return {
            "status": _connection_health(record, token_present, datetime.now(timezone.utc)),
            "member_name": record.get("member_name"),
            "member_id_suffix": str(record.get("member_id", ""))[-4:],
            "expires_at": expiry.isoformat() if expiry else None,
        }
    except PyMongoError as exc:
        raise ConnectionError("MongoDB connection status is unavailable") from exc
    finally:
        await client.close()
