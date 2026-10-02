"""One-use, browser-bound local HTTPS LinkedIn connection flow."""

import asyncio
import secrets
import time
from urllib.parse import urlencode, urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.integrations.linkedin import LinkedInError, exchange_code_details, member_identity
from app.storage.installation import installation_identity
from app.storage.linkedin_connection import ConnectionError, connection_status, save_connection


CALLBACK_PATH = "/api/v1/connections/linkedin/callback"
START_PATH = "/api/v1/connections/linkedin/start"


def run() -> None:
    settings = get_settings()
    redirect = settings.linkedin_redirect_uri or ""
    parsed = urlparse(redirect)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.path != CALLBACK_PATH
        or parsed.query
        or parsed.fragment
        or not all((settings.mongodb_uri, settings.mongodb_database,
                    settings.linkedin_client_id, settings.linkedin_client_secret))
        or not settings.app_tls_cert or not settings.app_tls_cert.is_file()
        or not settings.app_tls_key or not settings.app_tls_key.is_file()
    ):
        raise SystemExit("LinkedIn connection needs MongoDB, client credentials, and the registered local HTTPS callback")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    state = secrets.token_urlsafe(32)
    browser_secret = secrets.token_urlsafe(32)
    start_nonce = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + 1800
    started = False
    used = False
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    @app.get(START_PATH)
    async def start(request: Request) -> Response:
        nonlocal started
        if (started or time.monotonic() > expires_at or
                not secrets.compare_digest(request.query_params.get("nonce", ""), start_nonce)):
            return PlainTextResponse("Invalid or expired sign-in link", status_code=400)
        started = True
        auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
            "response_type": "code",
            "client_id": settings.linkedin_client_id,
            "redirect_uri": redirect,
            "state": state,
            "scope": "openid profile w_member_social",
        })
        response = RedirectResponse(auth_url, status_code=302)
        response.set_cookie(
            "linkedin_connect", browser_secret, max_age=1800,
            httponly=True, secure=True, samesite="lax", path=CALLBACK_PATH,
        )
        return response

    @app.get(CALLBACK_PATH)
    async def callback(request: Request) -> PlainTextResponse:
        nonlocal used
        valid = (
            started and not used and time.monotonic() <= expires_at
            and secrets.compare_digest(request.query_params.get("state", ""), state)
            and secrets.compare_digest(request.cookies.get("linkedin_connect", ""), browser_secret)
        )
        if not valid:
            return PlainTextResponse("Invalid or expired LinkedIn sign-in", status_code=400)
        used = True
        code = request.query_params.get("code")
        if not code:
            return PlainTextResponse("LinkedIn did not grant access", status_code=400)
        try:
            token = await exchange_code_details(
                settings.linkedin_client_id, settings.linkedin_client_secret, redirect, code
            )
            member = await member_identity(token.access_token)
            await save_connection(
                settings.mongodb_uri, settings.mongodb_database, installation_id,
                member, token,
            )
        except (LinkedInError, ConnectionError):
            return PlainTextResponse("LinkedIn connection failed; check local diagnostics", status_code=502)
        response = PlainTextResponse("LinkedIn connected. You may close this tab.")
        response.delete_cookie("linkedin_connect", path=CALLBACK_PATH)
        print(f"LinkedIn connection saved for member ID suffix {member['sub'][-4:]}", flush=True)
        return response

    start_url = f"https://{parsed.hostname}:{parsed.port or 443}{START_PATH}?nonce={start_nonce}"
    print("Open this one-use local sign-in link on this computer (keep it private):", flush=True)
    print(start_url, flush=True)
    uvicorn.run(
        app, host=parsed.hostname, port=parsed.port or 443,
        ssl_certfile=str(settings.app_tls_cert), ssl_keyfile=str(settings.app_tls_key),
        access_log=False,
    )


def status() -> None:
    settings = get_settings()
    if not settings.mongodb_uri or not settings.mongodb_database:
        raise SystemExit("MongoDB configuration is missing")
    installation_id = installation_identity(
        settings.app_data_dir, settings.mongodb_uri, settings.mongodb_database
    )
    try:
        result = asyncio.run(connection_status(
            settings.mongodb_uri, settings.mongodb_database, installation_id
        ))
    except ConnectionError as exc:
        raise SystemExit(str(exc)) from None
    print(f"LinkedIn status: {result['status']}")
    if result.get("member_name"):
        print(f"Account: {result['member_name']} (ID suffix {result['member_id_suffix']})")
    if result.get("expires_at"):
        print(f"Token expiry: {result['expires_at']}")
