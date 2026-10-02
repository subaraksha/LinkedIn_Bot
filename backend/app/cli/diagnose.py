import argparse
import asyncio
import json

from pydantic import BaseModel

from app.config import get_settings
from app.integrations.mongo import inspect_mongo
from app.integrations.telegram import TelegramClient
from app.storage.installation import installation_identity
from app.storage.linkedin_connection import connection_status


class ModelProbe(BaseModel):
    status: str
    count: int


class DraftProbe(BaseModel):
    post: str
    basis: str


async def inspect(live_model: bool = False) -> dict:
    settings = get_settings()
    result: dict = {
        "configuration": {"missing": settings.missing_configuration()},
        "mongo": {"status": "not_configured"},
        "telegram": {"status": "not_configured"},
        "gemini": {"status": "not_run"},
        "linkedin": {"status": "not_configured"},
    }
    if settings.mongodb_uri and settings.mongodb_database:
        try:
            result["mongo"] = {"status": "reachable", **await inspect_mongo(
                settings.mongodb_uri, settings.mongodb_database
            )}
        except Exception as exc:
            result["mongo"] = {"status": "failed", "error_type": type(exc).__name__}
    if settings.telegram_bot_token:
        client = TelegramClient(settings.telegram_bot_token)
        try:
            identity = await client.identity()
            webhook = await client.webhook_status()
            result["telegram"] = {
                "status": "identity_verified",
                "bot_id_suffix": identity["id"][-4:],
                "username": identity["username"],
                "webhook_configured": webhook["configured"],
                "pending_update_count": webhook["pending_update_count"],
            }
        except Exception as exc:
            result["telegram"] = {"status": "failed", "error_type": type(exc).__name__}
        finally:
            await client.close()
    if settings.mongodb_uri and settings.mongodb_database:
        try:
            installation_id = installation_identity(
                settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
            )
            connection = await connection_status(
                settings.mongodb_uri, settings.mongodb_database, installation_id
            )
            result["linkedin"] = {"status": connection["status"]}
        except Exception as exc:
            result["linkedin"] = {"status": "failed", "error_type": type(exc).__name__}
    if live_model and settings.gemini_api_key and settings.gemini_model:
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            try:
                reply = await client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents='Return status "ready" and count 1.',
                    config={"response_mime_type": "application/json", "response_schema": ModelProbe},
                )
                parsed = reply.parsed if isinstance(reply.parsed, ModelProbe) else ModelProbe.model_validate_json(reply.text or "")
                draft_reply = await client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=(
                        "Synthetic source: A developer is learning about message queues. "
                        "They have not deployed a queue to production. Write one LinkedIn-style "
                        "sentence, at most 220 characters, about learning why ordering matters. "
                        "Do not claim production experience. Set basis to exactly 'learning'."
                    ),
                    config={"response_mime_type": "application/json", "response_schema": DraftProbe},
                )
                draft = (
                    draft_reply.parsed if isinstance(draft_reply.parsed, DraftProbe)
                    else DraftProbe.model_validate_json(draft_reply.text or "")
                )
                draft_ok = (
                    bool(draft.post.strip()) and len(draft.post) <= 220
                    and draft.basis == "learning"
                    and "i deployed" not in draft.post.lower()
                    and "i built" not in draft.post.lower()
                )
                result["gemini"] = {
                    "status": "schema_and_synthetic_draft_validated"
                    if parsed.status == "ready" and parsed.count == 1 and draft_ok
                    else "unexpected_response"
                }
            finally:
                await client.aio.aclose()
        except Exception as exc:
            result["gemini"] = {"status": "failed", "error_type": type(exc).__name__}
    return result


def run() -> None:
    parser = argparse.ArgumentParser(description="Redacted phase-0 provider checks")
    parser.add_argument("--live-model", action="store_true", help="Make one Gemini API request")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(inspect(args.live_model)), indent=2))


if __name__ == "__main__":
    run()
