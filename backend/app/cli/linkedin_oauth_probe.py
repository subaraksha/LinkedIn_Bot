"""One-shot local HTTPS callback feasibility probe; never stores an access token."""

import secrets
from urllib.parse import urlencode, urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.integrations.linkedin import LinkedInError, exchange_code, member_identity


def run() -> None:
    settings = get_settings()
    redirect = settings.linkedin_redirect_uri
    parsed = urlparse(redirect or "")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.path != "/api/v1/connections/linkedin/callback"
        or not settings.linkedin_client_id
        or not settings.linkedin_client_secret
        or not settings.app_tls_cert
        or not settings.app_tls_key
        or not settings.app_tls_cert.is_file()
        or not settings.app_tls_key.is_file()
    ):
        raise SystemExit(
            "OAuth probe needs a registered local HTTPS redirect, client credentials, and TLS cert/key"
        )
    state = secrets.token_urlsafe(32)
    used = False
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get(parsed.path)
    async def callback(request: Request) -> PlainTextResponse:
        nonlocal used
        if used or not secrets.compare_digest(request.query_params.get("state", ""), state):
            return PlainTextResponse("Invalid or reused OAuth state", status_code=400)
        used = True
        code = request.query_params.get("code")
        if not code:
            return PlainTextResponse("LinkedIn did not return an authorization code", status_code=400)
        try:
            token = await exchange_code(
                settings.linkedin_client_id,
                settings.linkedin_client_secret,
                redirect,
                code,
            )
            identity = await member_identity(token)
        except LinkedInError:
            return PlainTextResponse("LinkedIn authorization failed; check terminal diagnostics", status_code=502)
        print(f"OAuth probe succeeded for member ID suffix {identity['sub'][-4:]}")
        return PlainTextResponse("LinkedIn local OAuth callback succeeded. You may close this tab.")

    auth_url = "https://www.linkedin.com/oauth/v2/authorization?" + urlencode({
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": redirect,
        "state": state,
        "scope": "openid profile w_member_social",
    })
    print("Open this URL in the browser on this computer (keep it private):")
    print(auth_url)
    uvicorn.run(
        app,
        host=parsed.hostname,
        port=parsed.port or 443,
        ssl_certfile=str(settings.app_tls_cert),
        ssl_keyfile=str(settings.app_tls_key),
        access_log=False,
    )
