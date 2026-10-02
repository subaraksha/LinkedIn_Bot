"""One-shot LinkedIn text post transport. Call only after durable send preflight."""

import re
from dataclasses import dataclass

import httpx

from app.domain.approval import PublicationEnvelope


POST_ID_RE = re.compile(r"\Aurn:li:(?:share|ugcPost):[0-9]+\Z")
API_VERSION_RE = re.compile(r"\A[0-9]{6}\Z")
LITTLE_RESERVED = frozenset("|{}@[]()<>#\\*_~")


class PostContractError(ValueError):
    pass


@dataclass(frozen=True)
class PostResult:
    outcome: str  # confirmed, definitive_failure, or unknown
    status_code: int | None
    post_id: str | None
    reason: str


def escape_little_text(text: str) -> str:
    """Encode literal approved text for LinkedIn's `little` commentary syntax."""
    return "".join("\\" + char if char in LITTLE_RESERVED else char for char in text)


def post_permalink(post_id: str) -> str:
    if not POST_ID_RE.fullmatch(post_id):
        raise PostContractError("Invalid LinkedIn post ID")
    return f"https://www.linkedin.com/feed/update/{post_id}"


def text_post_payload(envelope: PublicationEnvelope) -> dict:
    if (
        not envelope.author_urn.startswith("urn:li:person:")
        or not envelope.author_urn.removeprefix("urn:li:person:")
        or not envelope.text.strip()
        or envelope.visibility != "PUBLIC"
        or envelope.feed_distribution != "MAIN_FEED"
        or envelope.target_entities
        or envelope.third_party_distribution_channels
        or envelope.is_reshare_disabled_by_author
    ):
        raise PostContractError("Unsupported V1 LinkedIn publication envelope")
    return {
        "author": envelope.author_urn,
        "commentary": escape_little_text(envelope.text),
        "visibility": envelope.visibility,
        "distribution": {
            "feedDistribution": envelope.feed_distribution,
            "targetEntities": list(envelope.target_entities),
            "thirdPartyDistributionChannels": list(envelope.third_party_distribution_channels),
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": envelope.is_reshare_disabled_by_author,
    }


async def create_text_post(
    token: str,
    api_version: str,
    envelope: PublicationEnvelope,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PostResult:
    """Submit exactly once. Callers must never automatically retry an unknown outcome."""
    if not API_VERSION_RE.fullmatch(api_version):
        raise PostContractError("LinkedIn API version must use YYYYMM")
    payload = text_post_payload(envelope)
    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=False, transport=transport
    ) as client:
        try:
            response = await client.post(
                "https://api.linkedin.com/rest/posts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Linkedin-Version": api_version,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError:
            return PostResult("unknown", None, None, "transport_uncertain")
    if response.status_code == 201:
        post_id = response.headers.get("x-restli-id", "")
        if POST_ID_RE.fullmatch(post_id):
            return PostResult("confirmed", 201, post_id, "created")
        return PostResult("unknown", 201, None, "missing_or_invalid_post_id")
    if response.status_code in {400, 401, 403, 404, 422}:
        return PostResult("definitive_failure", response.status_code, None, "explicit_rejection")
    return PostResult("unknown", response.status_code, None, "unexpected_response")
