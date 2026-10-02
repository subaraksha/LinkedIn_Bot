from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import connections, knowledge
from app.api.security import COOKIE, local_request_guard, require_session, sessions
from app.config import get_settings
from app.integrations.telegram import TelegramClient, TelegramError
from app.integrations.telegram_pairing import mongo_client
from pymongo.errors import PyMongoError
from app.storage.installation import InstallationError, installation_identity
from app.storage.linkedin_connection import ConnectionError, connection_status

app = FastAPI(title="LinkedIn Post Agent", version="0.2.0", docs_url=None,
              redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.middleware("http")(local_request_guard)
app.include_router(connections.router)
app.include_router(knowledge.router)
dashboard_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (dashboard_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dashboard_dir / "assets"), name="assets")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "running"}


@app.get("/bootstrap")
def bootstrap(token: str) -> Response:
    session = sessions.consume_bootstrap(token)
    if not session:
        raise HTTPException(status_code=401, detail="Launcher link expired or was already used")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, session.id, httponly=True, secure=True,
                        samesite="lax", max_age=12 * 3600, path="/")
    return response


@app.get("/api/session")
def session_status(request: Request) -> dict:
    return {"csrf": require_session(request).csrf}


@app.post("/api/session/logout")
def logout(request: Request) -> Response:
    require_session(request, write=True)
    sessions.revoke(request.cookies.get(COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/readiness")
async def readiness(request: Request) -> dict:
    require_session(request)
    settings = get_settings()
    missing = settings.missing_configuration()
    linkedin = "not_connected"
    telegram = "not_paired"
    database = "not_configured"
    worker = "stopped"
    linkedin_account = None
    telegram_account = None
    if not {"MONGODB_URI", "MONGODB_DATABASE"} & set(missing):
        installation_id = None
        try:
            installation_id = installation_identity(
                settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
            )
            status = await connection_status(
                settings.mongodb_uri, settings.mongodb_database, installation_id
            )
            linkedin = status["status"]
            linkedin_account = status.get("member_name")
        except (ConnectionError, InstallationError, PyMongoError):
            linkedin = "unavailable"
        mongo = mongo_client(settings.mongodb_uri)
        try:
            owner = await mongo[settings.mongodb_database].owner_settings.find_one({"_id": "owner"})
            if not owner or owner.get("installation_id") != installation_id:
                raise InstallationError("Owner database binding needs attention")
            database = "connected"
            record = await mongo[settings.mongodb_database].connections.find_one({"_id": "telegram"})
            if (record and installation_id
                    and record.get("installation_id") == installation_id
                    and record.get("status") == "connected"):
                telegram = "paired"
                telegram_account = {"owner_id_suffix": str(record.get("sender_id", ""))[-4:],
                                    "bot_id_suffix": str(record.get("bot_id", ""))[-4:]}
                receiver = await mongo[settings.mongodb_database].telegram_receivers.find_one(
                    {"_id": record["bot_id"]}
                )
                if receiver and receiver.get("lease_until") and receiver["lease_until"].replace(
                    tzinfo=timezone.utc
                ) > datetime.now(timezone.utc):
                    worker = "running"
                if settings.telegram_bot_token:
                    provider = TelegramClient(settings.telegram_bot_token)
                    try:
                        identity = await provider.identity()
                        webhook = await provider.webhook_status()
                        if identity["id"] != record["bot_id"]:
                            telegram = "bot_mismatch"
                        elif webhook["configured"]:
                            telegram = "webhook_conflict"
                    except TelegramError:
                        telegram = "unavailable"
                    finally:
                        await provider.close()
                else:
                    telegram = "token_missing"
        except Exception:
            telegram = "unavailable"
            database = "unavailable"
        finally:
            await mongo.close()
    return {
        "status": "configuration_needed" if missing else "configured",
        "missing_configuration": missing,
        "database": database,
        "gemini": "configured" if not {"GEMINI_API_KEY", "GEMINI_MODEL"} & set(missing) else "not_configured",
        "telegram": telegram,
        "linkedin": linkedin,
        "worker": worker,
        "linkedin_account": linkedin_account,
        "telegram_account": telegram_account,
    }


@app.get("/")
def dashboard(request: Request) -> FileResponse:
    require_session(request)
    index = dashboard_dir / "index.html"
    if not index.is_file():
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Dashboard has not been built yet")
    return FileResponse(index)


def run() -> None:
    import uvicorn

    settings = get_settings()
    if (settings.app_host not in {"127.0.0.1", "localhost"}
            or not settings.app_tls_cert or not settings.app_tls_cert.is_file()
            or not settings.app_tls_key or not settings.app_tls_key.is_file()):
        raise SystemExit("The dashboard needs a loopback host and local HTTPS certificate/key")
    print("Open this one-use local dashboard link:", flush=True)
    print(f"https://{settings.app_host}:{settings.app_port}/bootstrap?token={sessions.bootstrap_token}", flush=True)
    uvicorn.run(app, host=settings.app_host, port=settings.app_port,
                ssl_certfile=str(settings.app_tls_cert),
                ssl_keyfile=str(settings.app_tls_key), access_log=False)


@app.on_event("shutdown")
async def stop_pairing_task() -> None:
    await connections.shutdown()
