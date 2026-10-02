from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.integrations.telegram_pairing import mongo_client
from app.storage.installation import InstallationError, installation_identity
from app.storage.linkedin_connection import ConnectionError, connection_status

app = FastAPI(title="LinkedIn Post Agent", version="0.1.0")
dashboard_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (dashboard_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dashboard_dir / "assets"), name="assets")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "running"}


@app.get("/api/readiness")
async def readiness() -> dict:
    settings = get_settings()
    missing = settings.missing_configuration()
    linkedin = "not_connected"
    telegram = "not_paired"
    if not {"MONGODB_URI", "MONGODB_DATABASE"} & set(missing):
        installation_id = None
        try:
            installation_id = installation_identity(
                settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
            )
            linkedin = (await connection_status(
                settings.mongodb_uri, settings.mongodb_database, installation_id
            ))["status"]
        except (ConnectionError, InstallationError):
            linkedin = "unavailable"
        mongo = mongo_client(settings.mongodb_uri)
        try:
            record = await mongo[settings.mongodb_database].connections.find_one({"_id": "telegram"})
            if (record and installation_id
                    and record.get("installation_id") == installation_id
                    and record.get("status") == "connected"):
                telegram = "paired"
        except Exception:
            telegram = "unavailable"
        finally:
            await mongo.close()
    return {
        "status": "configuration_needed" if missing else "configured",
        "missing_configuration": missing,
        "database": "configured" if not {"MONGODB_URI", "MONGODB_DATABASE"} & set(missing) else "not_configured",
        "gemini": "configured" if not {"GEMINI_API_KEY", "GEMINI_MODEL"} & set(missing) else "not_configured",
        "telegram": telegram,
        "linkedin": linkedin,
    }


@app.get("/")
def dashboard() -> FileResponse:
    index = dashboard_dir / "index.html"
    if not index.is_file():
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Dashboard has not been built yet")
    return FileResponse(index)


def run() -> None:
    import uvicorn

    settings = get_settings()
    if settings.app_host not in {"127.0.0.1", "::1"}:
        raise SystemExit("APP_HOST must be a loopback address")
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)
