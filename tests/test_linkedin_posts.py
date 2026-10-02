import json
import unittest

import httpx

from app.domain.approval import PublicationEnvelope
from app.integrations.linkedin_posts import (
    create_text_post, escape_little_text, text_post_payload,
)


class LinkedInPostsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.envelope = PublicationEnvelope(
            draft_id="d", draft_version=1,
            text="Line 1 🙂\nLine 2 #AI https://example.com/?a=1&b=2",
            author_urn="urn:li:person:member",
        )

    async def test_exact_payload_and_header_only_success(self):
        seen = []

        def respond(request):
            seen.append(request)
            return httpx.Response(201, headers={"X-Restli-Id": "urn:li:share:123456789"})

        result = await create_text_post(
            "fake-token", "202609", self.envelope,
            transport=httpx.MockTransport(respond),
        )
        self.assertEqual(result.outcome, "confirmed")
        self.assertEqual(result.post_id, "urn:li:share:123456789")
        self.assertEqual(len(seen), 1)
        payload = json.loads(seen[0].content)
        self.assertEqual(payload, text_post_payload(self.envelope))
        self.assertEqual(payload["commentary"],
                         "Line 1 🙂\nLine 2 \\#AI https://example.com/?a=1&b=2")
        self.assertEqual(seen[0].headers["Linkedin-Version"], "202609")
        self.assertEqual(seen[0].headers["X-Restli-Protocol-Version"], "2.0.0")

    async def test_missing_id_and_server_error_are_unknown(self):
        for response in (httpx.Response(201), httpx.Response(503)):
            result = await create_text_post(
                "fake-token", "202609", self.envelope,
                transport=httpx.MockTransport(lambda _request: response),
            )
            self.assertEqual(result.outcome, "unknown")

    async def test_explicit_rejection_is_definitive(self):
        result = await create_text_post(
            "fake-token", "202609", self.envelope,
            transport=httpx.MockTransport(lambda _request: httpx.Response(403)),
        )
        self.assertEqual(result.outcome, "definitive_failure")

    def test_reserved_punctuation_is_escaped_as_literal_text(self):
        self.assertEqual(
            escape_little_text(r"#tag *bullet* @user [x](url) {a|b} \_~<>"),
            r"\#tag \*bullet\* \@user \[x\]\(url\) \{a\|b\} \\\_\~\<\>",
        )
