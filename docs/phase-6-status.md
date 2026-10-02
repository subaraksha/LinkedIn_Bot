# Phase 6: pause, resume, history, and feedback

## Owner controls

The weekly invitation schedule can be paused and resumed from the dashboard. Pausing clears the next invitation without deleting the saved weekday, time, or timezone. Resuming calculates the next future invitation, so downtime never creates a backlog. Saving a schedule explicitly resets its paused state. The active conversation and drafts remain saved independently of the invitation schedule.

`CONTINUE` in the paired Telegram chat now recaps the saved stage, selected topic, and next action for an active conversation. It warns that messages sent during downtime may need to be resent and flags conversations older than seven days for a review of time-sensitive claims. If an exact preview or an unsent approval exists, `CONTINUE` invalidates it and cancels any pending publication job; a new `FINAL` preview and exact command are required. It never starts publication. `SKIP WEEK` and `DISCARD` close a draft conversation with distinct outcomes and cancel an unsent publication job. An already started or uncertain LinkedIn request remains subject to the Phase 5 recovery controls.

## History and future suggestions

The dashboard lists published, skipped, and discarded conversations, with dates and confirmed or owner-reported post links. An owner can open any listed conversation to review its immutable draft versions and the feedback that produced them. This endpoint is bound to the local owner session and installation.

Research already uses previous topic choices, rejected titles, and published post excerpts to avoid repetition. Draft generation now receives a bounded set of explicit feedback from previously published conversations as writing guidance. Current feedback takes priority, and previous feedback is never evidence for claims about the owner. The owner can still correct writing preferences directly in the dashboard.

## Verification and scope

The backend unit suite and frontend production build pass. Phase 6 adds direct checks for pause/resume scheduling, stale-progress recaps, and session/CSRF protection. The owner should verify the controls in the local dashboard and paired Telegram chat. Integration tests that create a disposable MongoDB database remain skipped on this installation because only the active owner database is available.

The previously deferred GitHub knowledge intake and validated knowledge import/round-trip remain outside the agreed V1 completion. They remain documented in the original PRD.
