# Integration validation — V1

Validation now targets one owner installation, as agreed in PRD 1.3. Phase 0's narrow integration journey is complete. Full V1 recovery, scheduling, onboarding, and failure-hardening work remains in later phases.

| Gate | Phase 0 status | Remaining V1 hardening |
| --- | --- | --- |
| G1 Telegram | `passed_phase0` (real owner pairing, durable inbound message, dry-run and final previews, exact approval, confirmation; temporary-database replay/rollback/restart checks; simulated rejected send and transport errors) | Full dashboard lifecycle, prolonged outage, blocked bot and rate-limit recovery, and broader RV04–RV12 failure testing |
| G2 LinkedIn | `passed_phase0` (local OAuth, saved connection, one exact approved application post; simulated one-shot unknown outcome and journal recovery) | Owner-facing uncertain-outcome resolution, live reconnect lifecycle, and broader RV01–RV12 failure testing |
| G3 MongoDB/Gemini | `passed_phase0` (real replica-set transactions/index/rollback in temporary database; structured Gemini and synthetic draft) | Recheck after configuration changes; broader model quality and quota behavior belong to later features |

## First-installation probes — 1 October 2026

- Runtime: macOS 27.0, Python 3.13.5; PyMongo 4.18.2, google-genai 1.75.0, httpx 0.28.1, FastAPI 0.142.2. Dependencies locked in `uv.lock`.
- Telegram `getMe` returned the configured bot identity (numeric ID redacted to suffix `2049`); `getWebhookInfo` showed no webhook and zero pending updates at the initial probe. No outbound message or failure-path test has run.
- The owner sent a one-use `/start` challenge to the configured bot. The candidate was saved through a transaction and explicitly confirmed by the owner; status readback showed `connected` for the paired numeric owner ID (suffix `2149`). The pairing listener is stopped.
- The owner sent `test message` through the paired private chat. The receiver stored the exact text, accepted it under the paired numeric sender/chat binding, committed its update ledger and next cursor together, and reported one owner input. After stopping and restarting the receiver, the message still had exactly one stored record; a simultaneous second receiver was rejected. The restart marked an approval barrier. No outbound delivery, invalid external sender, database outage, or full recovery test has run.
- Pure approval-contract tests reject changed text/account/version/challenge, undelivered preview, stale binding or receiver epoch, and vague or additional text. Approval intake and a guarded publisher are now wired to the Telegram receiver; a live approved post has not yet been attempted from the application.
- A dry-run preview fixture was written to MongoDB with an exact envelope hash and three ordered outbound messages. Telegram returned message IDs for the intro, exact draft body, and final no-publication notice; readback showed all three `accepted` and no approval challenge. The owner confirmed all three appeared in the paired chat. A fake delivery-error test shows that an ambiguous body send leaves the final control message pending and is not automatically retried. The dry run was deliberately non-publishing.
- MongoDB `ping` succeeded after configuring `certifi` as the TLS CA bundle. `hello` reported a replica set; the configured database had zero collections. No write, index, transaction, rollback, or installation-identity test has run.
- Gemini returned a nonempty response to a synthetic one-word prompt. Structured output, quota handling, and model quality have not been tested.
- LinkedIn OAuth was tested by the owner in Postman, including `userinfo`; owner reported a successful `rest/posts` response with `x-restli-id`. Do not repeat the live post merely to reproduce this report.
- Local HTTPS OAuth callback probe succeeded after the owner registered the exact redirect and trusted the local certificate. The callback exchanged a one-use code, read the member identity (suffix `WO5b`), and discarded the token. The probe cannot publish.
- Saved LinkedIn connection flow passed on this installation. The one-use browser-bound callback displayed success; a separate status check found the account identity (suffix `WO5b`), token reference in the OS credential store, MongoDB metadata, and a future token expiry. The temporary callback listener was stopped. Reconnect, revoked-token handling, account-change invalidation, and the app publisher remain untested.
- Deterministic authority/approval unit checks pass: wrong sender/chat, bot sender, edited and forwarded updates are rejected; publish commands must occupy the whole message; envelope hashes change with text/author/audience/distribution. Mocked publisher tests cover one confirmed send, uncertain outcome with no retry, and restart barrier. These are not a full polling or publishing integration test.
- Second installation: not tested.

## Exact approved application post — 2 October 2026

- The owner requested a final punctuation edit in Telegram. The first, unapproved challenge was invalidated; no publication attempt existed for it. A new three-part preview was delivered with the revised body ending in `shape!` and a new one-use command.
- The paired owner sent the new command. The receiver saved one owner message, approval intake created one receipt and publication job, and the publisher recorded one attempt before the outbound call.
- LinkedIn returned a confirmed post ID, `urn:li:share:7511661758080618496`. The workflow, approval, and attempt all read back as confirmed/published; the Telegram publication notice was accepted. [Open the published post](https://www.linkedin.com/feed/update/urn:li:share:7511661758080618496).
- The temporary receiver was stopped after confirmation. The local `.env` still has `PUBLISHING_ENABLED=false`; the live session used a process-scoped override.
- This validates one successful path on the owner installation. Network ambiguity, revoked token, and changed-account cases remain open.

## Single-installation Phase 0 checks — 2 October 2026

- The owner changed the V1 release target to one local installation; PRD and architecture revision 1.3 replace the two-installation acceptance condition. The existing owner pairing, LinkedIn connection, and successful post are retained as real integration evidence.
- A uniquely named temporary database on the configured Atlas cluster passed majority-acknowledged write, unique-index duplicate rejection, transaction rollback, durable Telegram inbox/sequence/cursor commit, duplicate provider-update rejection, restart approval invalidation, and a one-shot uncertain publication using a fake LinkedIn sender. The probe removed only its own test collections. It used the available cluster credentials, not separate test credentials, and never wrote to the active owner database.
- Gemini produced valid structured responses and a bounded synthetic learning-oriented post. This checks model/schema access with no personal profile material; it does not establish broad writing quality or quota headroom.
- LinkedIn and Telegram identity/status checks passed again. The current application post remains confirmed; no second live post was sent for these checks.
- In-memory failure simulations pass for a 503/unknown LinkedIn response, no automatic second send, restart after an unresolved send boundary, and recovery from a durable success journal. These are simulated failures, not another provider call.
- A reconnect now increments the LinkedIn binding revision and invalidates pending final previews and publication jobs. A controlled test proves an old approval cannot send after the binding changes. The reconnect itself has not been repeated against the live owner account.
- Telegram transport simulations for invalid token (401), blocked chat (403), rate limit (429), and poll connection loss produce visible errors without printing the bot token. They do not revoke or block the real bot.
- Local checks cover expired or missing LinkedIn credentials and refusal to connect a different member. No live token was expired, revoked, or replaced for these checks.
- The Python test suite passed 32 checks, and the frontend production build passed. A source scan found no credential-shaped values in shareable files. `.env`, local credentials, certificates, and generated data remain ignored by Git.
- Review cleanup added a transactional owner-sequence check at the publication send boundary, stricter stale-message handling, token-safe Telegram errors, and a versioned local installation binding that survives MongoDB password rotation. The existing local binding migrated to version 2; the saved LinkedIn connection still reads as connected. No new public post was sent.

For each actual run, record date, OS/runtime, dependency versions, redacted account identity, cases/results, failures, limitations and retest needs. Never record credentials or authorization codes.
