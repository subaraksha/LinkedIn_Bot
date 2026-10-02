import httpx


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str):
        self._token = token
        self._http = httpx.AsyncClient(timeout=15.0, follow_redirects=False)

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, payload: dict | None = None) -> dict | list:
        try:
            url = f"https://api.telegram.org/bot{self._token}/{method}"
            response = (
                await self._http.post(url, json=payload)
                if payload is not None
                else await self._http.get(url)
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError("Telegram request failed") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram returned an invalid response")
        if not response.is_success or not data.get("ok"):
            raise TelegramError(f"Telegram {method} failed with HTTP {response.status_code}")
        if "result" not in data:
            raise TelegramError("Telegram returned an invalid response")
        return data["result"]

    async def identity(self) -> dict:
        result = await self._call("getMe")
        if not isinstance(result, dict) or "id" not in result:
            raise TelegramError("Telegram identity response is invalid")
        return {"id": str(result["id"]), "username": result.get("username")}

    async def webhook_status(self) -> dict:
        result = await self._call("getWebhookInfo")
        if not isinstance(result, dict):
            raise TelegramError("Telegram webhook response is invalid")
        return {
            "configured": bool(result.get("url")),
            "pending_update_count": result.get("pending_update_count", 0),
        }

    async def poll(self, offset: int | None, timeout: int = 30) -> list[dict]:
        payload = {
            "timeout": timeout,
            "limit": 100,
            "allowed_updates": ["message", "edited_message"],
        }
        if offset is not None:
            payload["offset"] = offset
        try:
            response = await self._http.post(
                f"https://api.telegram.org/bot{self._token}/getUpdates",
                json=payload,
                timeout=httpx.Timeout(timeout + 10.0),
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramError("Telegram poll failed") from None
        if not isinstance(data, dict):
            raise TelegramError("Telegram poll returned an invalid response")
        if not response.is_success or not data.get("ok"):
            raise TelegramError(f"Telegram poll failed with HTTP {response.status_code}")
        if not isinstance(data.get("result"), list):
            raise TelegramError("Telegram poll returned an invalid response")
        return data["result"]

    async def send_text(self, chat_id: str, text: str) -> str:
        result = await self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        })
        if not isinstance(result, dict) or "message_id" not in result:
            raise TelegramError("Telegram send response is invalid")
        return str(result["message_id"])
