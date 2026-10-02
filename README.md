# LinkedIn Post Agent

Local scaffold for the single-owner V1 product. The current release target is one owner installation under PRD and architecture revision 1.4. Read the [PRD](docs/PRD_final.md), [architecture](docs/SystemDesign_Final.md), and [portable knowledge contract](docs/knowledge-portability.md) before Phase 2 work.

## Start the scaffold

1. Install Python 3.12 or 3.13, `uv`, and Node.js. The architecture targets Node.js 22; the frontend scaffold also supports the currently available Node.js 18.
2. Run `uv sync --locked` and `npm ci --prefix frontend`.
3. Run `npm run build --prefix frontend`.
4. Copy `.env.example` to an ignored local `.env`, fill the integration values, and set `APP_TLS_CERT` and `APP_TLS_KEY` to the local certificate and key described below.
5. Run `uv run linkedin-agent-foundation --apply` to bind the configured owner database.
6. Run `uv run linkedin-agent-api`, then open the one-use `https://127.0.0.1:8765/bootstrap?...` link printed by the launcher. The clean dashboard URL is shown after the link is consumed.

The dashboard shows missing variable names only, never values. The worker entry point is `uv run linkedin-agent-worker`; after owner pairing, it receives and durably stores direct owner messages. Publication remains off while `PUBLISHING_ENABLED=false`.

The API serves the built dashboard over local HTTPS. The launcher link establishes a twelve-hour browser session; restart the API to issue another one-use link after logging out or expiry. Dashboard writes require that session and a request token. The API exposes `/api/health` without private data and protects `/api/readiness`. After editing the frontend, rebuild it with `npm run build --prefix frontend` and reload the local HTTPS dashboard; account setup is intentionally bound to that origin.

The dashboard can now start Telegram pairing, display the private-chat candidate, and confirm the numeric owner account. It can also start LinkedIn authorization using the registered callback while the dashboard is running. After pairing, start the separate local receiver with `uv run linkedin-agent-worker`; the dashboard reports whether its lease is active. Connecting accounts does not publish anything. The earlier CLI flows remain available for diagnostics.

## Phase 1 database foundation

Run `uv run linkedin-agent-foundation --apply` after setting the MongoDB values in the ignored `.env`. It binds the configured database to this installation, fills missing owner metadata without replacing existing values, and creates the unique and lookup indexes needed for later features. The operation is repeatable. A conflicting owner binding or duplicate record stops setup instead of silently discarding data. The configured database is the only database changed by this command.

The job store in `backend/app/storage/jobs.py` supports durable enqueue, duplicate suppression, lease claims, stale-worker rejection, and lease recovery. Future research and draft jobs will use it; the existing Phase 0 publication path keeps its separate one-shot send rules.

To repeat the foundation integration check without touching the owner database, run `RUN_MONGO_INTEGRATION=1 uv run python -m unittest tests.test_foundation_integration -q`. It creates and removes a uniquely named temporary database on the configured cluster.

Starting the API does not send a Telegram message or publish to LinkedIn. Opening the dashboard checks connection status. The Phase 0 probes below include read-only provider checks and writes confined to a uniquely named temporary MongoDB database. Gate status is tracked in [integration validation](docs/integration-validation.md).

## Phase 0 probes

Run `uv run linkedin-agent-diagnose --live-model` to check the configured MongoDB connection, Telegram bot identity/webhook, saved LinkedIn connection, and two small synthetic Gemini requests. It prints no credentials and does not send a Telegram message or write a LinkedIn post. Then run `uv run linkedin-agent-mongo-capability-probe` to check majority writes, a unique index, transaction rollback, durable Telegram ingress, duplicate rejection, and restart invalidation in a uniquely named temporary database on the configured cluster. It removes only its temporary collections and never writes to the active owner database.

The local LinkedIn OAuth callback probe is `uv run linkedin-agent-oauth-probe`. Register **exactly** `https://127.0.0.1:8765/api/v1/connections/linkedin/callback` in the LinkedIn developer app and set the same value for `LINKEDIN_REDIRECT_URI` in the ignored local `.env`. The probe needs `APP_TLS_CERT` and `APP_TLS_KEY` pointing to a certificate/key for 127.0.0.1. To create a local certificate with that address in its subject alternative names:

```sh
mkdir -p .local-data/tls
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout .local-data/tls/localhost.key -out .local-data/tls/localhost.crt \
  -subj "/CN=localhost" -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
chmod 600 .local-data/tls/localhost.key
```

Trust `.local-data/tls/localhost.crt` using macOS Keychain Access before following the browser authorization link; do not bypass a browser certificate warning. Keep the private key local. The probe displays a one-use authorization URL, validates the callback and member identity, and discards the access token. It does not save a LinkedIn connection or publish. Keep the authorization URL and terminal output private.

The exact callback registration and certificate trust must be checked on the owner’s computer. Postman OAuth success does not prove the local callback works.

## Save a LinkedIn connection

Use the dashboard's Connect LinkedIn button while the main API is running. The callback checks the dashboard browser session and one-use state, retrieves the account identity, stores the access token in the OS credential store, and saves only connection metadata in the configured MongoDB database. It does not publish. The sign-in link expires after ten minutes. The older `uv run linkedin-agent-connect` command remains available if the main API is stopped, because both flows use the same callback port.

Run `uv run linkedin-agent-connection-status` to check the saved connection without displaying the token. The dashboard's setup status also shows whether LinkedIn is connected or needs reconnection. A different LinkedIn member is rejected during reconnect; account switching needs an explicit migration flow so pending approvals cannot silently change destination.

## Pair the owner Telegram account

Run `uv run linkedin-agent-telegram-pair start`. It verifies the configured bot and that no webhook is active, then prints a one-use `t.me` link. Open that link from the intended owner's Telegram account and press Start. The command records a candidate and exits; it does **not** pair the account. Check the displayed numeric user ID and name against your own Telegram account. Run `uv run linkedin-agent-telegram-pair status` to review the candidate again. Only after that local owner check, run `uv run linkedin-agent-telegram-pair confirm <numeric-user-id>`.

The pairing challenge expires after ten minutes. The receiver saves each update and its next polling offset together in MongoDB before acknowledging it to Telegram. It uses a short database lease to reject a competing receiver. Pairing is now available from the authenticated dashboard; the older CLI is retained for diagnostics.

After pairing, `uv run linkedin-agent-worker` starts the direct long-poll receiver. It verifies the bot and owner binding, marks a restart as an approval barrier, and stores accepted owner text with the update ledger and polling cursor in one transaction. Rejected updates retain only minimal metadata. With publication disabled, saved approval commands are rejected. Stop it with Ctrl+C.

The exact preview approval contract checks changed text, account, version, challenge, delivery state, owner binding, and receiver epoch. The worker can process an approval only for an active, fully delivered final preview while `PUBLISHING_ENABLED=true`. A restart invalidates the challenge. The publisher records a durable one-shot send boundary and never automatically retries an uncertain LinkedIn outcome.

Run `uv run linkedin-agent-preview-demo` to send one explicitly labeled dry-run preview to the paired Telegram chat. It stores the fixture draft and three ordered outbound messages before sending. The complete body is a separate plain-text message; the final notice is sent only after the body receives a Telegram message ID. An uncertain send is recorded and never automatically retried. This demo creates no approval challenge and cannot call LinkedIn.

For a live phase 0 post, save the exact UTF-8 body in a local ignored file. Set `PUBLISHING_ENABLED=true`, start the paired worker, then run `uv run linkedin-agent-final-preview --live <post-file>`. This sends an account and public audience notice, the exact body, and a one-use command to Telegram. Only the paired owner can send that command from the private chat. The command approves immediate public publication; an unapproved preview never reaches LinkedIn. Check the exact account and body in Telegram before sending it. If LinkedIn's result is uncertain, inspect the profile before any fresh preview.

Run the Phase 0 checks locally with `uv run python -m unittest discover -s tests -q` and `npm run build --prefix frontend`. The full V1 authoring and recovery workflow is still being built; the live-preview command above is a narrow validation tool.

### Professional information intake (Phase 2)

The local dashboard now accepts manually entered facts and pasted LinkedIn profile text. Pasted text is stored as a private source only; it is not automatically extracted into facts. A manually entered fact is marked confirmed because the owner supplied it, but defaults to **private** publication permission. Each fact retains a source reference and can be edited, disputed or confirmed, made public or private, and deleted from active knowledge. Editing records a new owner-statement source for the revised wording. These changes invalidate any waiting publication approval and pending publish job, requiring a new preview.

The app saves these records in the bound MongoDB database. Profile revision increments with each accepted change, and the portable export reflects the current revision. Resume upload, fact suggestions, guided questions, and export are described below. Optional GitHub intake is deferred by the owner. Use synthetic data for testing; the opt-in integration test creates and drops a uniquely named temporary database on the configured cluster.

### Fact suggestions from saved text

For a saved LinkedIn text source, choose **Suggest facts from this text**. The local backend sends that source text to the configured Gemini model, accepts only structured suggestions with a verbatim quote found in the source, and saves them as `pending_confirmation` and `private`. The dashboard shows the supporting quote and lets the owner confirm, edit, dispute, change publication permission, or delete each suggestion. Repeating the action on a completed source does not create duplicates. The model is never called just by saving a source. A confirmation does not publish a post.

### Resume import

The dashboard accepts PDF, DOCX, or UTF-8 plain text resumes up to 10 MiB. Uploading stores a generated-name original under the ignored, owner-only `APP_DATA_DIR/uploads` directory and saves bounded extracted text with page or section labels in MongoDB. It does not run Gemini or approve publication. Choose **Suggest facts from this source** to send extracted text to Gemini; accepted suggestions remain pending and private, with exact source quotes and page/section labels for review. Scanned PDFs, encrypted files, unsupported formats, malformed files, files without useful text, and over-limit files show an error. A single suggestion pass currently requires at most 100,000 extracted characters; longer parsed resumes remain saved but require a shorter text source for suggestions. The parser runs in a separate process with a wall timeout, CPU limit, and 512 MiB memory limit (supervised RSS on macOS).

### Portable knowledge downloads

The dashboard offers two separate ZIP downloads. **Full knowledge (private)** contains `profile.json`, `profile.md`, `profile.schema.json`, and a checksum `manifest.json`. It includes current confirmed, pending, and disputed facts, privacy labels, source metadata, supporting evidence excerpts, and deletion suppression markers. Original resume files are included only if the owner checks the explicit option. **Public profile** includes only facts that are both confirmed and marked public; it excludes private/pending/disputed facts, source text and excerpts, originals, and suppression markers. The public bundle is an input for a future portfolio, not an automatic publication.

The format is version 1 and its independent schema is published at `docs/profile.schema.json`. Bundle checksums are verified before a ZIP is saved. The latest default full bundle is refreshed after accepted profile changes; if that write fails, the fact change remains saved and the dashboard reports the export as outdated. Downloading generates a fresh committed snapshot. Bundles are written with owner-only permissions under ignored `APP_DATA_DIR/exports` and served only through the local authenticated dashboard. Re-import preview and apply are later Phase 2 work.

### Goals, preferences, and guided questions

The dashboard saves target roles, audience, interests, content goals, tone, length, technical depth, emoji/hashtag choices, styles/phrases/topics to avoid, confidential details, and optional writing samples. It suggests focused questions for missing goals and ambiguous team or learning claims. Answers and skips are durable; answers linked to uncertain facts add private owner evidence while those facts remain pending until confirmed. Preferences and clarification edits increment the profile revision and invalidate waiting publication approvals. The full private export includes these records; the public export includes only safe style settings. Explicit prohibited terms are checked again at final preview and publication preflight. Optional GitHub intake and knowledge re-import are deferred by the owner; see `docs/phase-2-status.md`.

### Weekly topics (Phase 3)

The dashboard can start topic research immediately or save an optional weekly Telegram invitation schedule. Invitations are off by default. The worker gathers bounded public-feed evidence, asks Gemini for four or five source-linked topic options, and lets the paired owner choose, request alternatives, provide an idea, or skip in Telegram. It then saves the owner's perspective and whether they have hands-on experience or are exploring the topic. Phase 3 ends at `READY_FOR_DRAFT`. See [Phase 3 status and commands](docs/phase-3-status.md) and edit [research sources](config/research-sources.json) to change the feeds.

### Drafts and approval check (Phase 4)

In Telegram, send `DRAFT` after selecting a topic, give feedback in ordinary words to create immutable new versions, use `RESTORE <version>` to copy an older version forward, and send `FINAL` for an exact three-part preview. The dashboard shows draft history. The preview command checks approval only: it records a `validated_only` receipt and never creates a LinkedIn publishing job. Phase 5 requires a fresh preview and approval. See [Phase 4 status and commands](docs/phase-4-status.md).

### Publishing and recovery (Phase 5)

The default publishing switch remains off. Once enabled for an owner-reviewed live test, a new `FINAL` preview explicitly states that its one-use Telegram command will publish publicly. The worker checks the exact saved draft, account, owner binding, and profile again before a single LinkedIn request. Confirmed posts appear with a link in the dashboard; explicit rejection preserves the draft. If the outcome is uncertain, the app blocks retries and guides the owner through publisher quiescence and an audited outcome resolution. See [Phase 5 status and recovery](docs/phase-5-status.md).
