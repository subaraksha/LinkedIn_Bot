# Phase 4: drafting and exact approval check

## Owner flow

1. Finish the Phase 3 topic conversation in Telegram. At `READY_FOR_DRAFT`, send `DRAFT`.
2. The worker uses the selected topic, its saved public research snapshots, your perspective, and confirmed facts you marked public. It checks publication boundaries and runs a separate grounding review. If the draft fails these checks, it is not shown as ready; send `DRAFT` to retry.
3. The bot sends version 1. Reply with ordinary feedback to create a new immutable version. Each version remains visible in the dashboard. Send `RESTORE <version>` to copy an earlier body into a **new** version.
4. Send `FINAL` to receive three Telegram messages: the LinkedIn account and public audience, the exact body, and a one-use command. The command is active only after all three messages are accepted by Telegram.
5. The `PUBLISH ...` command in **Phase 4 only validates** the exact approval flow. The bot confirms validation and creates a `validated_only` receipt. It does not enqueue a publish job or call LinkedIn. Phase 5 requires a fresh preview and approval before any live send.

`DISCARD` closes the conversation. If a profile boundary, LinkedIn binding, or Telegram receiver epoch changes, an open approval is invalidated. Send `FINAL` again after reviewing the draft; if your professional information changed, create a fresh draft first.

## Implementation notes

- Draft versions are append-only records in `draft_versions`. Generation has an ID and workflow revision guard, so stale model results cannot overwrite a newer owner response. Feedback received while generation runs is saved and cancels that result.
- Model prompts receive only confirmed public facts; private and pending facts stay out. Source excerpts come only from the selected topic's saved research snapshots. The generation response must use known fact and source IDs and pass the separate grounding check.
- Final preview binds the exact body, version, LinkedIn member, Telegram owner binding, receiver epoch, expiration, and an unpredictable challenge. Edited, forwarded, stale, duplicate, or mismatched commands cannot validate it.
- Delivery uncertainty stops the preview without an automatic resend. The owner can request a new `FINAL` preview.
- `PUBLISHING_ENABLED` stays disabled for Phase 4. The validation-only receipt has no publication job and is not accepted by the Phase 5 publisher.

## Verification

`python -m unittest discover -s tests` exercises the approval-only route, stale body rejection, and existing publication safety rules. The UI build checks the dashboard draft history. A live owner walkthrough should check drafting, a feedback revision, restore, final preview, and validation in Telegram; it should also confirm that no LinkedIn post appears.
