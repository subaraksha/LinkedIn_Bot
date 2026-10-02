import unittest

import httpx

from app.integrations.telegram import TelegramClient, TelegramError


class TelegramTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_token_blocked_chat_and_rate_limit_are_visible_without_token(self):
        for status in (401, 403, 429):
            client = TelegramClient("private-test-token")
            await client._http.aclose()
            client._http = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(status, json={"ok": False})
                )
            )
            try:
                with self.assertRaises(TelegramError) as caught:
                    await client.send_text("paired-chat", "preview")
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn("private-test-token", str(caught.exception))
            finally:
                await client.close()

    async def test_poll_transport_failure_does_not_return_updates(self):
        def fail(_request):
            raise httpx.ConnectError("offline")

        client = TelegramClient("private-test-token")
        await client._http.aclose()
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        try:
            with self.assertRaises(TelegramError) as caught:
                await client.poll(123, timeout=1)
            self.assertEqual(str(caught.exception), "Telegram poll failed")
        finally:
            await client.close()

    async def test_malformed_provider_response_fails_safely(self):
        client = TelegramClient("private-test-token")
        await client._http.aclose()
        client._http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
        )
        try:
            with self.assertRaises(TelegramError) as caught:
                await client.identity()
            self.assertEqual(str(caught.exception), "Telegram returned an invalid response")
        finally:
            await client.close()
