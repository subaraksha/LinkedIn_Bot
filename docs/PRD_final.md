# Product Requirements Document
# Weekly LinkedIn Post Agent — V1

**Version:** 1.4<br>
**Date:** 2 October 2026<br>
**Status:** Revised V1 scope — one local owner installation with direct Telegram integration<br>
**Audience:** Product owner, Codex, and other coding agents

## 1. Purpose and context

Build a single-owner LinkedIn content assistant that runs on the owner’s computer. The owner works in backend development and AI. Recommendations and drafts must reflect the owner’s supplied experience, interests, contributions, opinions, and writing preferences.

The owner wants better career opportunities and a credible way to demonstrate technical knowledge. The product should reduce the effort required to discover relevant topics, develop a personal angle, write a post, and publish it consistently.

The agent learns from professional information supplied by the owner, proposes four or five topics weekly, gathers optional personal input, drafts a post, and revises it through a Telegram conversation. It publishes to the user's LinkedIn profile only after explicit approval of the exact final version.

The owner also has a web dashboard to review and correct what the application understands about them, manage preferences and connections, and inspect drafts and publishing history.

This document records the agreed product behavior and operating constraints: local application execution, one owner and one configured MongoDB connection, Gemini API access using an owner-supplied key, and a portable owner-controlled knowledge export. Messaging uses the official Telegram Bot API with one owner-controlled bot identity and token. OpenClaw and WhatsApp are not part of V1. Detailed architecture, collection schemas, framework choices, and installation procedures belong in a separate system-design document. V1 is the release name; 1.4 is this document revision. This revision retains the single-installation decision from 1.3. Architecture revision 1.4 is the matching implementation baseline.

## 2. Product objectives

1. Help the owner identify topics relevant to their experience, interests, and target roles.
2. Help the owner demonstrate technical understanding through explanations, lessons, and trade-offs grounded in their actual knowledge.
3. Support consistent weekly posting while preserving the user's control over content and publication.
4. Make the application's understanding of the owner visible and correctable.
5. Keep the weekly interaction convenient by conducting selection, drafting, revisions, and approval through Telegram.

Recruiter interest and technical credibility are the intended outcomes. V1 does not promise job opportunities, reach, or engagement, and does not include measurement of those outcomes.

## 3. Agreed scope and operating principles

| Area | V1 decision |
| --- | --- |
| Users | One local installation, one owner, one professional profile |
| Application hosting | Runs on the owner’s computer; no cloud application deployment |
| Database | MongoDB; the owner supplies one cluster connection and database configuration |
| Configuration | One local `.env`; share only a placeholder `.env.example` |
| Data location | Profile, workflow, draft, and history records in the configured MongoDB cluster; original uploaded files remain local |
| AI | Gemini API using the owner’s supplied API key and available quota; no local model required |
| Cost constraint | No additional paid services or required paid-tier upgrades beyond the supplied Gemini API entitlement |
| Availability | Scheduled work depends on the local application being running, the computer being awake, and required services being reachable |
| Primary audience goal | Attract relevant recruiters and demonstrate technical credibility |
| Topic domains | Backend engineering, AI, and the owner’s confirmed interests |
| Cadence | One weekly topic shortlist and a workflow aimed at one post per week |
| Shortlist size | Four or five topics |
| Active work | One active post workflow at a time |
| Web experience | Onboarding, knowledge review, preferences, account connections, drafts, and history |
| Weekly interaction | Private Telegram bot conversation, from invitation through approval and publication confirmation |
| Bot identity | One owner-controlled bot; no competing receiver |
| Messaging connection | Official Telegram Bot API directly from the local backend, using long polling; no OpenClaw, webhook, public tunnel, or hosted gateway |
| Post format | Text, with optional links and hashtags |
| Publishing destination | The owner’s connected LinkedIn profile |
| Publishing time | Immediately after explicit approval, subject to a successful connection and publication |
| Personal input | Optional, with focused questions to help the user contribute |
| Shared resources | Application code and default source definitions may be shared; no shared research service |
| Private resources | The installation uses its configured MongoDB connection, Telegram bot/token and paired owner IDs, owner profile, account authorisations, conversations, drafts, and approvals |
| Human control | No publication based on silence, topic selection, or ambiguous feedback |

Each suggested angle and draft must be based on the owner’s profile, research, and input. Years of experience alone must not be treated as evidence of expertise or specific achievements.

## 4. End-to-end user journey

### Initial setup

1. The owner runs the application locally, supplies their MongoDB connection and Gemini API key through their own local configuration, and opens the local dashboard. No shared registration or user-selection flow is required.
2. They upload a resume, provide relevant LinkedIn text, optionally supply selected public repositories, and add any missing information.
3. The agent asks focused questions to clarify the user's background and goals.
4. The user reviews extracted facts, inferences, and supporting evidence in the knowledge dashboard.
5. The user corrects the profile, defines publication boundaries and writing preferences, connects LinkedIn, and chooses a weekly invitation day and time.
6. The owner creates a bot through Telegram’s BotFather and stores its token in their local configuration. A bot does not need its own phone number; the owner still needs a Telegram account. They open the bot chat, press Start, and complete the owner-pairing flow in the local dashboard. [T1]
7. The dashboard shows the verified bot identity and paired owner conversation. The application is ready only when polling and sending work for that owner.

### Weekly cycle

1. The user receives a Telegram invitation to review their weekly topics.
2. After engaging, they receive four or five suggestions with relevance explanations, possible angles, and sources.
3. They select a topic, request alternatives, propose their own topic, or skip the week.
4. The agent asks one or two focused questions and accepts optional personal input.
5. The agent creates a draft and revises it based on feedback.
6. The user receives the exact final preview, version identifier, and destination account.
7. The user explicitly approves that version for publication.
8. The approved text is published to their LinkedIn profile, and the user receives confirmation and a post link.
9. The outcome is recorded in their dashboard and informs future suggestions.

The user may pause or cancel before publication. If an earlier workflow is unfinished when another weekly cycle arrives, the user is offered the choice to continue it or discard it before starting another.

## 5. Functional requirements

The following 14 features preserve the agreed product development order. All are within V1. Acceptance criteria describe observable behavior, not implementation choices.

### F01. Single-owner workspace and independent configuration

**Purpose:** Give the owner a self-contained installation with their data and connected accounts.

**Requirements**

- Maintain one owner profile, career-goal set, preference set, conversation history, and post history per installation.
- Read that owner's MongoDB connection and database name from local environment configuration; no shared connection string is embedded in the code.
- Connect exclusively to the configured database. Do not fall back to another database if configuration fails.
- Keep the owner’s Gemini key, LinkedIn authorisation, and Telegram bot token private to the local installation.
- Associate the installation with its bot ID, authorised numeric Telegram user ID, private chat ID, and connected LinkedIn account. Usernames and display names are informational, not authorisation checks.
- Establish the owner binding using a short-lived pairing challenge generated by the authenticated local dashboard, sent by the owner in the bot’s private chat, and confirmed in that dashboard. Do not make the first person to message a discoverable bot its owner.
- Allow only one active polling receiver per bot token. A second local instance must fail visibly rather than compete for incoming messages.
- Show connection status and any action needed to restore a connection.
- Keep real credentials out of version control; provide only placeholder configuration for sharing the application.

**Scope boundary:** One owner per installation. No application registration, multi-user login, persona switching, team roles, shared editing, cross-user approvals, or cross-installation synchronisation. The editable professional knowledge profile remains required. Local access and external sender checks are not removed by this simplification.

**Acceptance criteria**

- The code runs on the owner’s computer with local environment configuration and one MongoDB database.
- The installation reads and writes only its configured owner database and records.
- The owner can use the dashboard without selecting or creating a user in a shared application.
- A connection error is shown clearly and does not trigger fallback to another database or expose credentials.
- Only the authorised owner's messages can control the workflow; publication uses that owner's connected LinkedIn account.
- The owner retains a knowledge dashboard, preferences, drafts, and history.
- The owner can complete setup with their Telegram account and bot, without an additional bot phone number, OpenClaw installation, or a public endpoint.
- Messages from another person, another chat, a group, or a channel cannot control the workflow or view private content.
- Stopping the installation preserves its durable work for later restart.
- A duplicate receiver or incompatible webhook configuration is reported as a connection problem, not silently ignored.

### F02. Resume and professional information onboarding

**Purpose:** Collect the material needed for relevant and credible recommendations.

**Requirements**

- Accept a resume upload and relevant LinkedIn profile content supplied as text.
- Accept selected public GitHub repositories as optional supporting context.
- Allow manual addition of education, work experience, projects, tools, technologies, and achievements.
- Allow information to be added or updated after onboarding.
- Use repository descriptions and README content within the agreed GitHub scope.

**Scope boundary:** No automatic collection of a complete LinkedIn profile, private repository access, or detailed code analysis. Users do not need to provide all three sources.

**Acceptance criteria**

- A user can begin with a resume or manually supplied professional information without providing GitHub repositories.
- Supported source content contributes to the reviewable knowledge profile.
- Later additions and updates can be reviewed and corrected through the same knowledge experience.

### F03. Guided questions to fill knowledge gaps

**Purpose:** Resolve ambiguity without making onboarding an exhaustive interview.

**Requirements**

- Ask focused questions about missing or unclear information.
- Clarify individual contributions versus team contributions.
- Distinguish professional experience from learning or experimentation.
- Ask about target roles, audience, interests, and experiences suitable for public discussion.
- Allow questions to be skipped and answered later.
- Start topic suggestions once enough confirmed context exists; unanswered questions must not automatically prevent progress.

**Scope boundary:** A short initial conversation and relevant follow-ups as needed. No requirement to establish a complete biography before use.

**Acceptance criteria**

- Unclear project ownership leads to a clarification rather than an invented personal achievement.
- A skipped question remains unresolved rather than being treated as answered.
- The user can receive useful suggestions with a partially completed but usable profile.

### F04. Reviewable professional knowledge dashboard

**Purpose:** Make the application's understanding visible and correctable.

**Requirements**

- Display extracted facts about experience, education, skills, projects, and achievements.
- Distinguish confirmed information from inferences awaiting review.
- Show supporting evidence, such as a resume passage, repository content, or user answer.
- Highlight missing and conflicting information.
- Allow individual entries to be confirmed, edited, or deleted.
- Allow entries to be marked suitable for posts or private context only.
- Show when information was last updated.
- Give user corrections priority over earlier interpretations.
- Let the owner download a complete, reusable copy of their current stored professional knowledge and writing preferences in both structured and readable formats. Keep fact status, evidence, provenance, privacy permission, revision, and update timestamps needed to interpret the current profile.
- Provide a separate public-only export suitable as input to a portfolio; exclude private facts, pending inferences, confidential boundaries, and private source excerpts.
- Keep a current local export after accepted profile changes and show when export generation is behind the saved database revision.

**Scope boundary:** Show and export stored facts and explicit inferences, not internal model reasoning or unstored model memory. Uploaded material is not automatically approved for public use. A portable export is not live synchronisation with other applications; external edits require a validated import rather than silently changing the active profile.

**Acceptance criteria**

- A user can distinguish a confirmed skill from an inferred interest.
- The user can inspect why an entry exists and correct it.
- Corrections are reflected in subsequent recommendations and drafts; deleted entries are no longer used as active profile facts.
- Information marked private does not appear in generated public post content.
- Unresolved conflicts are visible and are not silently converted into confirmed claims.
- The owner can open the full export without this application, and another program can validate and read its documented, versioned structured format. A readable Markdown copy contains the same current profile facts with their status and privacy labels.
- Full export preserves evidence links and current preferences without credentials, tokens, approval challenges, or unrelated raw conversation history. Deleted claims are absent from active exports and cannot be silently reintroduced by import.
- The public-only export contains only confirmed, explicitly public facts and safe writing information; a private fact or source excerpt never appears in it.
- If export generation fails after a profile edit, the edit remains saved, the dashboard labels the local export as outdated, and the owner can retry. A fresh export reflects the latest committed profile revision.
- A validated export can be read or imported into an empty test installation without losing fact status, provenance, or publication permission; import never restores publishing approval.

### F05. Content goals, writing preferences, and boundaries

**Purpose:** Align content with each user's goals, voice, and publication limits.

**Requirements**

- Record target roles, interests, and the goals of attracting recruiters and demonstrating technical credibility.
- Capture preferences for tone, length, technical depth, emojis, and hashtags.
- Accept optional writing samples.
- Record phrases, styles, and topics to avoid.
- Record confidential details, project names, or business information that must not appear publicly.
- Allow these preferences to be reviewed and edited.

**Scope boundary:** One editable preference set per user. Style preferences do not permit unsupported personal claims.

**Acceptance criteria**

- Drafts use the relevant user's current preferences.
- Updating a preference affects subsequent drafting and revision.
- Prohibited details remain excluded even if present in an uploaded source.

### F06. Weekly topic discovery and personalised shortlist

**Purpose:** Identify useful topics and credible angles worth discussing.

**Requirements**

- Research recent developments relevant to the user's profile and interests.
- Produce four or five suggestions per user each week.
- For each suggestion, explain the topic, why it is timely or useful, why it fits the user, and a possible post angle.
- Include supporting source links.
- Consider previous posts and rejected suggestions to reduce repetition.
- Use relevant evergreen lessons or project insights when current news does not provide strong options.
- Personalise topic ranking and angles using the installation owner’s profile and independently collected research.
- Use publicly accessible sources without requiring paid search services; selected feeds, release notes, accessible pages, and user-supplied links are the proposed discovery inputs.
- Do not enable paid search grounding or other separately charged research tools automatically on the assumption that Gemini token quota covers them.
- Avoid labelling a topic as trending without supporting evidence.

**Scope boundary:** A weekly shortlist based on accessible selected sources, not continuous trend monitoring, exhaustive internet search, or guaranteed popularity ranking. No central shared research service. Specific source selection remains an implementation decision.

**Acceptance criteria**

- Each shortlist contains four or five options with sources, user relevance, and an angle.
- A user can understand why a topic was recommended from the explanation provided.
- Suggestions consider that user's prior content and feedback.
- Weak news relevance can result in an evergreen suggestion rather than an exaggerated trend claim.

### F07. Weekly Telegram invitation and topic selection

**Purpose:** Start the weekly interaction in the user's normal messaging experience.

**Requirements**

- Let each user choose a weekly invitation day and time.
- Send an invitation and present the shortlist after the user engages.
- Support selecting a topic, requesting alternatives, proposing a topic, and skipping the week.
- Allow at most one reminder for an unanswered weekly invitation. The owner must have started and paired their bot chat before invitations are enabled. Handle a blocked bot, invalid token, or messaging limit without repeated notification floods.
- Maintain one active workflow per user.
- Offer to continue or discard unfinished work before starting a new workflow.
- Preserve schedules across restarts and detect missed work on startup. Consolidate missed slots into at most one current invitation; do not send a backlog. Preserve any existing active workflow. Use the system-design default of one reminder after 48 hours only when the invitation remains unanswered and has not been superseded.

**Scope boundary:** The schedule controls invitations, not future publication of approved posts. Silence is never approval.

**Acceptance criteria**

- Each installation uses its owner’s schedule when the local application and required connections are available. No exact-time delivery is promised while the computer is asleep or the application is stopped.
- A topic selection starts the next step for the correct user.
- Skipping does not generate or publish a post for that cycle.
- An unfinished draft is not overwritten by a new weekly invitation.
- No further reminders are sent for that unanswered invitation after the single reminder.

### F08. Personal perspective collection

**Purpose:** Bring the user's own understanding into the post.

**Requirements**

- Ask one or two focused questions after topic selection.
- Accept opinions, observations, concerns, examples, practical lessons, notes, and relevant links.
- Clarify whether the user has hands-on experience or is exploring the topic.
- Allow the user to proceed without contributing additional input.

**Scope boundary:** Text-based Telegram input. No voice-note workflow. Lack of personal input does not permit invented experience.

**Acceptance criteria**

- Supplied perspectives are considered in the draft.
- A user can request a draft without answering the questions.
- If hands-on experience is unconfirmed, the draft does not claim the user implemented or used the technology.

### F09. Personalised draft creation

**Purpose:** Produce a usable LinkedIn post grounded in sources and the user's background.

**Requirements**

- Create a clear opening, main discussion, and closing.
- Use the selected topic, research, confirmed profile facts, preferences, and optional user input.
- Include relevant personal examples only when supported.
- Explain technical ideas and trade-offs clearly.
- Include links where useful and hashtags where appropriate to preferences.
- Keep factual claims aligned with available evidence.
- Do not invent achievements, metrics, project outcomes, or experience.

**Scope boundary:** One text-only draft at a time, with optional links and hashtags. No images, carousels, videos, or document posts.

**Acceptance criteria**

- The draft reflects the selected topic and user's preferences.
- Personal claims are supported by confirmed information or explicit input.
- When evidence is insufficient, unsupported claims are omitted or clarified rather than fabricated.
- Producing a draft does not trigger publication.

### F10. Conversational revisions and draft versions

**Purpose:** Let the user refine the post naturally while retaining prior work.

**Requirements**

- Accept ordinary feedback through Telegram, including requests to shorten, explain, remove, add, or rewrite content.
- Accept new examples and observations during revision.
- Support changes to one section or the full post.
- Ask for clarification when feedback is ambiguous.
- Maintain numbered versions and make earlier versions available in the dashboard.
- Allow the user to return to a previous version.
- Continue until the user approves, pauses, or discards the draft.

**Scope boundary:** Every revised or restored version remains subject to explicit final approval. No fixed number of revision rounds is specified for V1.

**Acceptance criteria**

- Feedback produces an identifiable updated version without losing earlier versions.
- The user can review and return to an earlier version.
- Ambiguous feedback is not interpreted as permission to publish.
- Revision alone never results in publication.

### F11. Exact final preview and explicit approval

**Purpose:** Ensure the user controls precisely what is published and where.

**Requirements**

- Show the complete final text, including links and hashtags.
- Identify the draft version and destination LinkedIn account.
- Offer clear actions to publish, continue editing, or cancel.
- Require an explicit publishing command tied to the displayed workflow, version, and one-use approval challenge, such as `PUBLISH W12 V3 ABC123`. The example code is illustrative; the real challenge is generated securely.
- Accept publication commands only as new, direct text messages from the paired owner in the paired private bot chat. Forwarded messages, quoted approval text, edited-message events, and messages from unpaired senders cannot authorise publication. An edited prior message is not a new approval; ask the owner to send a fresh command.
- Send the final preview as literal text. If multiple messages are needed, keep part labels and approval instructions distinct from the post body and issue the approval challenge only after every preview part is accepted for sending. The interface must not silently rewrite the text.
- Invalidate pending approval if the bot identity, owner binding, destination LinkedIn account, relevant content, or audience changes. Token rotation for the same bot requires reconnection checks and fresh preview/approval before a pending publication proceeds.
- Require fresh approval after any change to approved content.

**Scope boundary:** Approval covers one exact version of one post. Account connection, topic selection, silence, and ambiguous praise do not authorise publication.

**Acceptance criteria**

- The approved content is exactly the content presented in the final preview.
- “Looks better” or similar feedback does not publish a post.
- An approval for a superseded version does not publish the newer version.
- Changing text, links, or hashtags after approval requires another explicit approval.
- Only the owner of the workflow can approve its post.
- A new owner message containing the exact valid command can approve; a forwarded command or an edit to an older message cannot.
- A preview delivery failure or uncertain send does not produce an actionable approval challenge.
- Duplicate Telegram updates and repeated commands cannot cause duplicate publication.

### F12. LinkedIn publication and confirmation

**Purpose:** Complete the workflow by publishing the approved text to the correct account.

**Requirements**

- Publish the exact approved content immediately after explicit approval when the local application and required connections are available.
- Use the approving user's connected LinkedIn profile.
- Return confirmed success and the published post link.
- Prevent repeated approval messages from creating duplicate posts.
- Clearly distinguish failed or uncertain outcomes from confirmed publication.
- Preserve the draft if LinkedIn needs to be reconnected.
- Treat an uncertain publication outcome as unresolved; do not blindly retry a request that might already have created a post.

**Scope boundary:** No future publishing schedule. No automatic post-publication editing or deletion feature is included.

**Acceptance criteria**

- A successful post matches the approved version and destination.
- Repeating approval for an already published version does not create another post.
- Success is not reported without confirmation.
- A failed or uncertain outcome is communicated without losing the draft or falsely marking it published.
- A connection problem prompts reconnection while preserving the user's work.

### F13. Pause, resume, skip, and cancel

**Purpose:** Support an asynchronous conversation without forcing completion.

**Requirements**

- Preserve the selected topic, personal input, and latest draft between sessions.
- Resume at the previous stage and provide a brief recap when useful.
- Let the user skip the current week or discard the active draft.
- Let the user pause future weekly invitations and resume them later.
- Flag time-sensitive content for another review when it has become stale during a long pause.

**Scope boundary:** No forced completion deadline and no publication due to inactivity. Resume requires the local application, MongoDB, and relevant external services to be reachable. The application is locally run, not fully offline. Saved database progress survives downtime, but messages that Telegram has not yet delivered to the application have a separate provider retention limit. Do not promise recovery of every message sent while the laptop was off. [T2]

**Acceptance criteria**

- A user returning after several days can continue from previously saved input and drafts. If a message was sent while the application was offline and was never saved, the user may need to resend it.
- The dashboard and bot recap distinguish saved progress from potentially missed input after downtime. Invalidate pending approval after a known polling gap; show the saved draft and require a fresh preview/approval before publishing.
- Missing messages cannot be inferred from silence, and the system must not claim it has detected every lost update.
- Pausing future invitations stops them until the user resumes.
- Skipping or discarding does not publish content.
- If stale content is changed, the updated version requires approval before publication.

### F14. Post history and feedback-based improvement

**Purpose:** Make past work accessible and improve future relevance.

**Requirements**

- Show the active workflow stage and latest draft.
- List published, skipped, and discarded outcomes.
- Retain published links and draft versions.
- Remember previously covered topics.
- Use explicit feedback, topic choices, and edits to improve future recommendations and writing.
- Let users review and correct saved preferences.

**Scope boundary:** No LinkedIn engagement analytics, recruiter tracking, or automatic optimisation based on external performance metrics.

**Acceptance criteria**

- The dashboard reflects the current stage and records the outcome of completed or abandoned cycles.
- Published entries include the confirmed post link.
- Earlier drafts remain accessible as agreed in F10.
- Corrected preferences guide subsequent suggestions rather than an older conflicting preference.

## 6. Cross-feature rules

These rules apply to all 14 features and do not introduce additional feature scope.

1. **Owner installation:** The application uses the owner’s configured database and credentials. There is no user directory or persona-selection flow.
2. **Truthful personal claims:** Distinguish confirmed experience, inferred interests, and subjects the user is learning about.
3. **User corrections prevail:** Explicit corrections take priority over earlier extraction or inference.
4. **Publication boundaries apply throughout:** Information marked private or prohibited must not appear in public draft content.
5. **One active workflow:** New weekly activity must not silently overwrite unfinished work.
6. **Approval is version-specific:** Only the explicitly approved version may be published. Any subsequent content change invalidates that approval for the changed content.
7. **No silent publication:** Silence, account connection, a topic choice, or vague positive feedback cannot approve a post.
8. **No duplicate publication:** Repeated messages must not create duplicate posts from the same approved version.
9. **Honest outcomes:** Failed or uncertain publication must not be presented as success.
10. **Durable progress:** Successfully saved progress must survive restarts and inactivity. A failed database write must not be presented as saved. The application must not publish when it cannot verify the saved draft and approval.
11. **Telegram isolation:** Each installation has one private bot and one paired owner. No shared token, shared update receiver, shared messaging relay, or cross-owner routing is part of V1.
12. **Durable message acceptance:** Only advance the polling acknowledgement past updates whose durable disposition has been saved. Duplicate deliveries must not duplicate workflow actions. Messages still held only by Telegram are not durable application progress.
13. **No added paid dependency:** Do not activate paid hosting, database upgrades, search tools, or model fallbacks without a separate user decision. If required quota or a free-tier limit blocks work, show the limitation and pause the affected operation.

## 7. Operating and integration constraints

These constraints update the earlier shared-application assumptions without finalising a detailed system design.

### Local application and separate MongoDB connections

- The dashboard, application logic, scheduling, and background processing run on the owner’s computer. No cloud application deployment is required.
- The owner supplies `MONGODB_URI` and `MONGODB_DATABASE` in local environment configuration.
- MongoDB is the agreed database direction. SQLite and SQL-based storage are not the selected direction for this project.
- The owner may use an Atlas Free cluster. Atlas is cloud-hosted storage: using it does not mean that profile facts, drafts, or workflow records remain on the computer.
- Original uploaded documents stay in local application storage. Extracted information stored in MongoDB and content sent to Gemini leave the computer as required by those services.
- Each installation retains its own LinkedIn tokens, Gemini key, Telegram bot token, and paired owner IDs. These are not shared through the source repository or a common configuration file. Telegram receives the messages sent through its service; direct integration does not make the conversation local-only.
- Database availability and free-tier limits apply. The product must not quietly upgrade to paid infrastructure or substitute another database.
- A full application account-management system is unnecessary. One owner profile and sender/account verification remain necessary.

### Model integration and cost boundary

- Use Gemini through an owner-supplied API key. Local model inference is not required.
- Available API quota or credits are a user-supplied prerequisite, not something this application supplies or guarantees.
- Do not assume a consumer subscription or text-generation token allowance automatically covers API usage, embeddings, or search grounding.
- The target is no additional paid service dependencies beyond the supplied Gemini entitlement. Free-tier limits and provider entitlements remain external conditions, not a promise of unlimited free operation.
- Do not silently switch to a paid model/provider, paid search service, or paid infrastructure when quota is exhausted. Report the issue and pause the affected work.
- Vector storage and vector search are possible future reasons for choosing MongoDB, but embeddings, a vector index, and RAG are not required for V1.

### Telegram — direct bot integration

**Selected approach:** The local backend communicates directly with the official Telegram Bot API. No OpenClaw, WhatsApp account linking, spare bot number, messaging LLM, or separate messaging gateway is required.

- Create one owner-controlled bot through BotFather. Keep its token private to this installation.
- Receive messages through `getUpdates` long polling and send through the Bot API. The laptop initiates outbound HTTPS requests; Telegram does not need to call a local webhook. Long polling and webhooks cannot receive updates simultaneously for the same bot. [T2]
- Start one polling receiver for the configured bot. Verify the token’s bot identity at setup and startup. Treat polling conflicts as a visible configuration error; do not use the same token in another bot service.
- On detecting a configured webhook, explain that it conflicts with this installation’s polling mode. Provide a deliberate setup action to remove it while preserving pending updates; do not silently take over an unrelated bot integration or discard its queue.
- Pair through the local dashboard as specified in F01. Store the verified bot/user/chat IDs; normal workflow control uses only the paired owner’s private chat. Re-pairing requires local owner action and invalidates outstanding approvals.
- Treat incoming messages as untrusted data. Restrict normal input to supported text/link messages; do not fetch or execute arbitrary attachments. Reject groups, channels, bot senders, forwarded publication commands, and edited-message approvals.
- Persist a received update and its processing disposition before advancing the receiver’s saved acknowledgement position. A crash after persistence may replay the update, but must not repeat its action. On database failure, stop advancing acknowledgements and show that input is not yet saved. Handle irrelevant/unauthorised updates without storing unnecessary private message bodies or blocking later authorised input.
- Ordinary bot messaging is available without a paid bot subscription; paid broadcast features are not used. Provider limits still apply. Back off on rate limiting and surface blocked/invalid connections. [T1]
- A bot send accepted by Telegram means accepted, not read by the owner. An uncertain send must not cause unlimited retries, and uncertain preview delivery must not enable publishing. Save progress independently of notification success.
- Keep the bot token out of source control, browser responses, logs, diagnostics, and error URLs. In particular, redact API request URLs containing the token. The bot needs no LinkedIn publishing credentials; only the publication service may use them.

**Configuration contract:** The owner supplies `TELEGRAM_BOT_TOKEN` alongside `MONGODB_URI`, `MONGODB_DATABASE`, and `GEMINI_API_KEY`. Store the verified `telegram_bot_id`, `telegram_owner_user_id`, and `telegram_owner_chat_id` with the installation identity. These IDs are not secrets, but must not be accepted from unverified chat claims. Share only placeholders in `.env.example`.

**Offline behavior:** Telegram retains incoming bot updates for no longer than 24 hours. Previously saved drafts remain available, but unreceived updates can expire. The application must show downtime/reconnection status and ask the owner to resend missing input when needed. It must not promise recovery of all messages after prolonged downtime. [T2]

### Integration validation gates

| Gate | V1 validation requirement |
| --- | --- |
| **G1 — Telegram** | The owner’s bot starts, pairs securely, polls without a public endpoint, sends exact previews, rejects unauthorised/forwarded/edited approvals, deduplicates replayed updates, and resumes from saved progress. Verify crash-before/after-save behavior, database outage, duplicate receiver, webhook conflict, blocked bot, invalid token, rate limits, and potentially expired offline input. |
| **G2 — LinkedIn** | Verify the owner’s actual scopes/identity, local authorisation callback, explicitly approved text publication, success evidence, expired access, and uncertain-outcome recovery. The channel change does not remove this gate. |
| **G3 — MongoDB/Gemini** | Verify separate database access, durable state changes and required database capabilities, supplied Gemini credentials/quota, and validated model output. |

G1 now validates Telegram; the former OpenClaw/self-chat gate is obsolete. None of these gates is marked passed by updating this PRD. The real owner installation must be tested. Mocks are useful during development but are not proof of release readiness. Do not send live messages or publish test posts without the owner’s applicable authorization.

### LinkedIn

- Each installation connects and authorises its owner's LinkedIn account using the official API route.
- Permission to connect and publish on behalf of an account is distinct from approval of an individual post.
- Connecting LinkedIn does not import the user's full career history. Onboarding uses supplied sources.
- If the connection becomes invalid, request reconnection and preserve the draft.
- Validate the required application permissions and a compatible local authorisation callback before treating the integration as complete. Do not assume a plain HTTP localhost callback is accepted or quietly introduce a hosted callback service.
- Unknown publication outcomes require reconciliation rather than blind retries.

### Availability and open setup decisions

- The computer must be awake, connected, and running the application to execute scheduled research, messaging, and publication.
- When the application resumes, it must detect unfinished or missed work without overwriting the active workflow or treating inactivity as approval.
- Target operating systems and installation packaging remain implementation checks. Messaging is decided: direct Telegram, one owner-controlled bot, long polling, no self-chat, and no public messaging endpoint. Missed-invitation behavior is defined in F07. G1–G3 remain validation tasks.
- Python/FastAPI and other architectural components discussed so far remain proposals unless separately agreed. This PRD does not lock a frontend, worker framework, collection schema, or runtime packaging choice.

## 8. Explicitly outside V1

- Application registration, multi-user login, persona switching, team roles, collaboration, and cross-user approvals.
- Cloud application deployment, a shared backend, automatic cross-installation synchronisation, and a shared research service.
- WhatsApp/OpenClaw integration, shared bot identities/tokens, spare-number setup, hosted messaging gateways, public messaging webhooks/tunnels, and switching channels automatically.
- Telegram groups/channels, Mini Apps, paid broadcasts, voice messages, or multiple bot identities inside one installation.
- Guaranteed recovery of incoming messages that expired before the application saved them.
- Required paid infrastructure, paid search subscriptions, paid-tier database upgrades, and automatic paid fallbacks.
- Local model hosting, embeddings, vector indexing, and RAG as V1 requirements.
- Guaranteed operation while the local computer is asleep, disconnected, or the application is stopped.
- Complete automatic LinkedIn profile collection.
- Private GitHub repository access and detailed code analysis.
- Continuous trend monitoring or guarantees that topics are popular.
- Multiple concurrent post workflows for one user.
- Voice-note input.
- Images, carousels, videos, or document posts.
- Posting to other social platforms.
- Automatic comment replies or engagement activity.
- Future-time scheduling of approved posts.
- Post-publication editing or deletion workflows.
- LinkedIn engagement analytics, recruiter tracking, and performance-driven automatic optimisation.
- Publication without approval or after an inactivity deadline.

These exclusions are scope boundaries, not a committed roadmap for later releases.

## 9. V1 completion criteria

V1 is complete when the owner can run the application on their computer, using local configuration and the configured MongoDB cluster, and complete the following journey. Online operations are assessed while the local application and required services are available:

1. Supply professional information and answer or skip clarification questions.
2. Inspect the application's extracted facts, inferences, and evidence; correct the profile and set publication boundaries.
3. Set their preferences and weekly invitation schedule, and connect their own accounts.
4. Receive four or five personalised suggestions through Telegram with relevance explanations, angles, and sources.
5. Select a topic, optionally contribute a perspective, receive a draft, and revise it through Telegram.
6. Pause and resume without losing work, or skip or discard without publishing.
7. Review the exact final version and explicitly approve publication to their own LinkedIn profile.
8. Receive a confirmed publication result and link, or an accurate explanation of a problem with the draft preserved.
9. Review their current workflow, previous drafts, preferences, and publishing history in the dashboard.
10. Download the full portable knowledge bundle and a separate public-only profile; verify the bundle reflects their latest accepted corrections and privacy settings.

Completion also requires successful G1–G3 validation on the owner installation, secure private-chat owner pairing, and proof that a stop/restart preserves progress. Verify that a duplicate update, unauthorised sender, forwarded command, or edited message cannot publish; a known polling gap requires a fresh approval. The installation must access only its configured database without a user-management flow. Unsupported personal achievements must not be invented, changed drafts require fresh approval, and repeated approvals must not produce duplicate posts. Database or quota failures must be reported without falsely claiming saved progress or successful publication. A stop/restart must preserve previously saved workflow progress, and a missed schedule must not cause a backlog of automatic posts.

## 10. Instructions for coding agents using this PRD

- Treat revision 1.4 as the current V1 product scope. It adds portable owner knowledge to the 1.3 single-installation baseline and retains the exclusions of WhatsApp, OpenClaw, self-chat, and a shared bot number. Use one owner-controlled Telegram bot.
- The existing system architecture revision 1.1 has not been rewritten by this PRD update. Before coding its messaging path, replace its OpenClaw gateway/adapter, QR/session setup, WhatsApp environment variables, self-chat rules, and old G1 with this Telegram contract. Preserve the existing approval, worker, MongoDB, and LinkedIn safeguards where compatible.
- Build for one owner with local environment configuration and a configured MongoDB connection. Do not add account registration or persona switching.
- Keep the owner knowledge profile and review dashboard; removing multi-user management does not remove personalisation.
- Preserve the distinction between locally running application code and Atlas-hosted data.
- Do not replace the selected MongoDB direction with SQLite, or introduce paid dependencies, without a new user decision.
- Use a direct Telegram adapter within the local backend and a single long-polling receiver per bot. No messaging agent framework or public webhook is needed. Preserve update deduplication and durable acknowledgement ordering.
- Treat the Telegram integration tests, local LinkedIn authorisation, and G3 readiness as validation tasks until recorded evidence passes.
- Keep requirement IDs F01–F14 when planning work or reporting completion so implementation can be checked against the approved features.
- Use the listed feature order as the agreed product capability sequence; technical dependency planning does not change the scope.
- Derive implementation choices from these requirements without introducing additional user-facing features.
- Preserve the human approval rules even when automating the rest of the workflow.
- Do not infer personal experience from years of experience alone, or use one user's profile to fill gaps in the other's.
- Do not treat excluded capabilities as prerequisites for V1.
- Where an unspecified detail materially changes product behavior, identify it for clarification rather than silently expanding the requirements.
- This document authorises no real messages or posts by itself; real publication must follow the user's explicit approval within the product workflow.


## 11. Revision history

### Revision 1.4 — 2 October 2026

- Added a portable full knowledge export and a separate public-only profile under F04, with versioned structured data, readable Markdown, provenance, privacy labels, and explicit export freshness.
- Added independent validation/import into an empty test installation to Phase 2 acceptance. Export/import does not create publication approval or cross-application synchronisation.

### Revision 1.3 — 2 October 2026

- Changed the V1 release target from two independent owner installations to one owner installation at the user’s request.
- Kept the single-owner model, private Telegram pairing, configured MongoDB connection, exact approval, and provider safety requirements.
- Removed two-computer acceptance gates while retaining restart, isolation from unconfigured databases, and one-receiver checks.



### Revision 1.2 — 1 October 2026

- Replaced WhatsApp/OpenClaw with direct Telegram Bot API integration throughout the active requirements, journey, feature acceptance criteria, and coding-agent instructions.
- Selected one distinct bot/token per owner and per local installation; removed the shared-bot and self-chat assumptions. No extra phone number is required for a bot.
- Selected local long polling, with no cloud deployment, public webhook, tunnel, or shared messaging gateway.
- Added secure owner pairing, private-chat-only access, bot identity verification, and duplicate-receiver/webhook-conflict handling.
- Added durable update handling and clear downtime behavior, including Telegram’s limited retention of unreceived updates and fresh approval after a known polling gap.
- Preserved all 14 feature IDs, MongoDB isolation, Gemini usage, LinkedIn publishing, and exact-version human approval; specified rejection of forwarded and edited-message approvals.
- Replaced G1 with Telegram validation; retained G2/G3. All gates still require actual tests.
- Carried forward the established one-current-invitation catch-up and one-reminder defaults into F07.
- Flagged the existing architecture’s messaging sections as stale so coding agents do not implement its superseded OpenClaw route.

### Revision 1.1 — 30 September 2026

- Reframed the product as a single-owner application installed independently by two people, replacing the shared multi-user operating model.
- Added separate environment configuration and owner-specific MongoDB clusters/connections; removed the need for registration, multi-user login, roles, and persona switching.
- Preserved the editable owner profile, knowledge dashboard, all 14 feature IDs, and exact-version human approval.
- Clarified that original uploads remain local while Atlas records and Gemini requests use external services.
- Recorded MongoDB and owner-supplied Gemini API access as the agreed directions, with no local model requirement.
- Recorded OpenClaw as the proposed WhatsApp route pending validation; removed mandatory Meta Cloud API templates and the 24-hour-window workflow from that proposed route.
- Added the no-additional-paid-services constraint, quota/connection failure behavior, and research without required paid search tools.
- Clarified local availability, restart recovery, and missed schedules; retained unresolved setup details as open points rather than silently finalising them.
- Removed assumptions of centrally shared research; both installations operate independently.
- Kept vectors/RAG outside required V1 scope and left detailed architecture for a separate document.

## 12. Provider references for the Telegram decision

Checked on 1 October 2026. These sources describe provider behavior; ownership rules, approval policy, recovery UX, and acceptance tests above are application requirements. No live integration test was performed during this document update.

- **T1 — Telegram bot setup and capabilities:** [Bots: An introduction for developers](https://core.telegram.org/bots). Bot accounts, BotFather, initial user contact, and basic bot-platform availability.
- **T2 — Telegram transport contract:** [Telegram Bot API — Getting updates](https://core.telegram.org/bots/api#getting-updates) and [getUpdates](https://core.telegram.org/bots/api#getupdates). Polling, acknowledgement offsets, webhook exclusivity, and update-retention limits.
