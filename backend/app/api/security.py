"""Local HTTPS session and request protections."""

import secrets
import hashlib

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.local_session import BrowserSession, LocalSessions


COOKIE = "owner_session"
sessions = LocalSessions()


def session_digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def require_session(request: Request, *, write: bool = False) -> BrowserSession:
    session = sessions.get(request.cookies.get(COOKIE))
    if not session:
        raise HTTPException(status_code=401, detail="Open the current local launcher link")
    if write and not secrets.compare_digest(
        request.headers.get("x-csrf-token", ""), session.csrf
    ):
        raise HTTPException(status_code=403, detail="Invalid dashboard action token")
    return session


async def local_request_guard(request: Request, call_next):
    settings = get_settings()
    allowed = {f"{settings.app_host}:{settings.app_port}"}
    host = request.headers.get("host", "")
    if host not in allowed:
        return JSONResponse({"error": "Invalid local host"}, status_code=400)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("origin") != f"https://{host}":
            return JSONResponse({"error": "Invalid origin"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response
