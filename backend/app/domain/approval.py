import hashlib
import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


PUBLISH_RE = re.compile(r"\APUBLISH ([A-Z0-9]+) V([1-9][0-9]*) ([A-Z0-9]+)\Z", re.ASCII | re.IGNORECASE)


class PublicationEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    draft_id: str
    draft_version: int = Field(ge=1)
    text: str
    author_urn: str
    visibility: str = "PUBLIC"
    feed_distribution: str = "MAIN_FEED"
    target_entities: tuple[str, ...] = ()
    third_party_distribution_channels: tuple[str, ...] = ()
    is_reshare_disabled_by_author: bool = False

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class PublishCommand:
    workflow_code: str
    draft_version: int
    challenge_code: str


def parse_publish_command(text: str) -> PublishCommand | None:
    """Only a standalone new owner message can authorize publication."""
    match = PUBLISH_RE.fullmatch(text)
    if not match:
        return None
    return PublishCommand(match[1].upper(), int(match[2]), match[3].upper())
