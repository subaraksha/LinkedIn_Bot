import httpx
from dataclasses import dataclass


class LinkedInError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    expires_in: int | None
    granted_scopes: str | None


async def exchange_code_details(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> OAuthToken:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as http:
        try:
            response = await http.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LinkedInError("OAuth code exchange failed") from exc
    if response.status_code != 200 or not payload.get("access_token"):
        raise LinkedInError(f"OAuth code exchange rejected with HTTP {response.status_code}")
    raw_expiry = payload.get("expires_in")
    try:
        expiry = int(raw_expiry) if raw_expiry is not None else None
    except (TypeError, ValueError):
        expiry = None
    return OAuthToken(
        access_token=payload["access_token"],
        expires_in=expiry if expiry is not None and expiry > 0 else None,
        granted_scopes=payload.get("scope") if isinstance(payload.get("scope"), str) else None,
    )


async def exchange_code(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> str:
    return (await exchange_code_details(client_id, client_secret, redirect_uri, code)).access_token


async def member_identity(token: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as http:
        try:
            response = await http.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LinkedInError("LinkedIn identity lookup failed") from exc
    if response.status_code != 200 or not payload.get("sub"):
        raise LinkedInError(f"LinkedIn identity lookup rejected with HTTP {response.status_code}")
    return {"sub": str(payload["sub"]), "name": payload.get("name")}
