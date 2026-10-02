# Phase 5: approved LinkedIn publishing and uncertain-outcome recovery

## Publishing contract

Phase 5 reuses the Phase 0 one-shot LinkedIn Posts transport and publication journal. `PUBLISHING_ENABLED=false` remains the default. While it is false, `FINAL` still issues a validation-only preview and cannot create a publish job. When enabled, a **new** `FINAL` preview clearly says the exact command will publish publicly. A Phase 4 `validated_only` receipt never enters the publisher.

A live Telegram approval is accepted only after all three preview messages are confirmed, the exact standalone command matches the fresh challenge, the direct paired owner and receiver epoch match, and the draft version, text, envelope, LinkedIn member, connection revision, and profile revision remain current. Approval consumption, receipt, workflow transition, and one publication job commit together. Feedback, restore, `FINAL` replacement, or discard before the send boundary cancels that unsent job in the same transaction.

Before sending, the worker rechecks credentials, owner input order, profile boundaries, exact saved draft/preview, destination account, receiver lease, and recovery block. It records a unique durable attempt and process identity before making one external request. A confirmed response with a valid `x-restli-id` records the post URL. An explicit rejection preserves the draft for fresh review. A transport or ambiguous response becomes `PUBLISH_UNKNOWN`; the app does not retry it automatically. A local fsynced success journal preserves confirmed response evidence across a database interruption.

## Uncertain-outcome recovery

The dashboard shows the uncertain attempt. The owner first checks LinkedIn, then uses **Stop old publisher for recovery**. This blocks new sends, stops the attempt's worker and any currently registered local worker using their PID and process creation time, acquires the exclusive OS lock, retires the stopped receiver lease, and records quiescence. If any proof fails, publishing stays blocked.

After quiescence, the owner can record either:

- **Published:** supply a canonical LinkedIn post URL unless saved provider evidence already contains the post ID. Owner supplied URLs are labeled `owner_reported`, not API verified.
- **Not published:** explicitly acknowledge that manual inspection is inconclusive. The old attempt and approval are retired. No replacement job is created; a new `FINAL` preview and Telegram approval are required.

The same operation is available to a local launcher through `linkedin-agent-recovery-quiesce --attempt <attempt-id>`. Recovery cannot publish. After either resolution, restart the local worker before continuing Telegram actions; the dashboard can remain open.

## Checks

The unit suite uses a fake publisher and tests exact-body single sending, stale approval rejection, no job for Phase 4 receipts, unknown outcomes without retry, journal recovery, canonical owner post URLs, quiescence requirements, and idempotent resolution. The dashboard build checks the recovery controls. Disposable MongoDB integration tests remain optional because this installation has no separate test database. A real post still requires the owner's fresh Telegram command after seeing its exact public preview.
