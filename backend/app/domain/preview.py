"""Exact preview approval rules, independent of any transport or publisher."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.domain.approval import PublicationEnvelope, parse_publish_command
from app.domain.telegram import InboundText, OwnerBinding


@dataclass(frozen=True)
class PendingPreview:
    workflow_code: str
    challenge_code: str
    envelope_hash: str
    draft_version: int
    author_urn: str
    bot_id: str
    sender_id: str
    chat_id: str
    binding_revision: int
    receiver_epoch: int
    expires_at: datetime
    body_delivery_confirmed: bool
    control_delivery_confirmed: bool
    consumed: bool = False


@dataclass(frozen=True)
class ApprovalReceipt:
    workflow_code: str
    envelope_hash: str
    draft_version: int
    author_urn: str
    provider_message_id: str
    provider_update_id: int


def approve_preview(
    pending: PendingPreview,
    message: InboundText,
    binding: OwnerBinding,
    receiver_epoch: int,
    envelope: PublicationEnvelope,
    now: datetime | None = None,
) -> ApprovalReceipt | None:
    """Require one fresh direct owner command for one unchanged delivered envelope."""
    current_time = now or datetime.now(timezone.utc)
    command = parse_publish_command(message.text)
    if (
        pending.consumed
        or not pending.body_delivery_confirmed
        or not pending.control_delivery_confirmed
        or pending.expires_at <= current_time
        or pending.bot_id != binding.bot_id
        or pending.sender_id != binding.sender_id
        or pending.chat_id != binding.chat_id
        or pending.binding_revision != binding.revision
        or pending.receiver_epoch != receiver_epoch
        or message.sender_id != binding.sender_id
        or message.chat_id != binding.chat_id
        or pending.envelope_hash != envelope.digest()
        or pending.draft_version != envelope.draft_version
        or pending.author_urn != envelope.author_urn
        or command is None
        or command.workflow_code != pending.workflow_code
        or command.draft_version != pending.draft_version
        or not secrets.compare_digest(command.challenge_code, pending.challenge_code)
    ):
        return None
    return ApprovalReceipt(
        workflow_code=pending.workflow_code,
        envelope_hash=pending.envelope_hash,
        draft_version=pending.draft_version,
        author_urn=pending.author_urn,
        provider_message_id=message.message_id,
        provider_update_id=message.update_id,
    )
