# Phase 3: weekly topic conversation

Phase 3 implements PRD F06–F08 on the single-owner installation. It researches selected public feeds, proposes four or five source-linked ideas, sends a Telegram invitation, accepts a topic choice or owner-supplied idea, and saves the owner's perspective and explicit experience status. It does not create a LinkedIn draft or publish a post; those are later phases.

## Owner setup

1. Keep the local API and Telegram worker running. The worker performs research, checks the weekly schedule, and sends topic messages.
2. Review `config/research-sources.json`. It starts with public Python, TypeScript, GitHub, OpenAI, and Google Developers feeds. Each feed URL must use HTTPS and match its configured domain. Adjust the feeds in that file and restart the worker to use changes.
3. In the dashboard, choose a weekday, local time, and IANA timezone. Invitations are disabled until **Send me a weekly invitation in Telegram** is checked and the schedule is saved. The next invitation time is displayed.
4. **Find topics now** starts a single manual cycle for testing. It does not turn on weekly invitations.

The worker consolidates missed weeks into one current cycle after downtime. If work is already active, it offers to continue or discard instead of replacing it. The first invitation is sent only after a grounded shortlist is saved. One reminder may be sent 48 hours after an accepted invitation if the owner has not replied. Messages with an uncertain Telegram send result are shown in the dashboard and are never retried automatically.
Temporary Telegram polling failures create a receiver gap barrier and are retried with bounded delay. Saved research continues while Telegram reconnects; a publication approval invalidated by the gap cannot be reused.

## Telegram conversation

Reply `TOPICS` to see the current numbered shortlist. Each option has a why-now reason, a relevance explanation, an angle, and a source link. For the first list, `CHOOSE 1` or just `1` selects option one. After alternatives refresh the list, use the displayed revision, such as `CHOOSE 2:1`, so an old reply cannot select a different topic. `ALTERNATIVES` requests a new research pass. `MY TOPIC: <idea>` provides your own topic, including when research cannot find four reliable options. `SKIP WEEK` or `DISCARD` closes the current cycle without drafting or publishing.

After a choice, the bot asks for a perspective, observation, concern, or example. `SKIP INPUT` is allowed. After a supplied perspective, the bot asks for `HANDS ON` or `EXPLORING`; skipping leaves experience unconfirmed. The saved result enters `READY_FOR_DRAFT`. Draft generation begins in Phase 4 and must not turn an exploratory topic into a hands-on claim.

## Research boundaries

The local source file sets a 14-day recent window, a 90-day evergreen window, at most 40 feed candidates, and 12 detailed page fetches. Public DNS targets are checked and pinned; redirects stay within the configured domain; response size and time are bounded. Dates absent from a feed remain unknown. Every suggested source ID must refer to a saved research snapshot, and the exact URLs are assembled by the application from those snapshots. A weak shortlist becomes a recoverable failure rather than fabricated links or a claim of trending popularity.

Research can send public source excerpts, owner interests and target roles, and brief prior-post excerpts to the configured Gemini model. It does not send credentials. Only a paired Telegram owner can change the conversation through messages, and schedule changes require the local dashboard session and CSRF token.
Research attempts record the configured model, task, start/end times, result, and provider token counts when returned. Transient or weak-result failures retry at most three times with bounded delays before showing a recoverable failure.

## Verification

Unit checks cover source URL restrictions, timezone and daylight-saving calculations, and bounded shortlist messages. A disposable MongoDB integration test covers schedule persistence, missed-week consolidation, research job completion, versioned topic selection, perspective capture, and single-send outbox behavior. A bounded live check against the configured public feeds and Gemini model produced four suggestions with source IDs from those feeds. It used a synthetic profile; it did not send a Telegram message or publish to LinkedIn.
