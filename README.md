# LinkedIn Post Agent

Local scaffold for the single-owner V1 product. The current release target is one owner installation under PRD and architecture revision 1.3. Read the [PRD](docs/PRD_final.md) and [architecture](docs/SystemDesign_Final.md) before feature work.

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
