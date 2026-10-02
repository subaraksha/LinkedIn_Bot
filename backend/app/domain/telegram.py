from dataclasses import dataclass


@dataclass(frozen=True)
class OwnerBinding:
    bot_id: str
    sender_id: str
    chat_id: str
    revision: int


@dataclass(frozen=True)
class InboundText:
    update_id: int
    message_id: str
    sender_id: str
    chat_id: str
    text: str


def authorized_text(update: dict, binding: OwnerBinding) -> InboundText | None:
    """Return only a new direct private message from the paired human owner."""
    if "edited_message" in update or "message" not in update:
        return None
    message = update["message"]
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    if (
        chat.get("type") != "private"
        or sender.get("is_bot") is not False
        or str(sender.get("id")) != binding.sender_id
        or str(chat.get("id")) != binding.chat_id
        or "forward_origin" in message
        or "forward_date" in message
        or not isinstance(message.get("text"), str)
        or not isinstance(update.get("update_id"), int)
        or not isinstance(message.get("message_id"), int)
    ):
        return None
    return InboundText(
        update_id=update["update_id"],
        message_id=str(message["message_id"]),
        sender_id=binding.sender_id,
        chat_id=binding.chat_id,
        text=message["text"],
    )
