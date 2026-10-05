# LinkedIn Post Agent

A local, single-owner assistant that turns fresh AI and backend engineering sources into relevant LinkedIn topic ideas, evidence-backed drafts, and owner-approved posts.

## The problem

Finding something useful to share takes more than following the latest news. Topics can be too generic or too advanced for the author's experience, drafts can sound impersonal, and useful engineering solutions can get lost behind jargon or product announcements. Keeping sources, personal context, revisions, and publication decisions together adds more manual work.

## Our solution

The agent combines public-source discovery with an editable professional profile. It selects topics that fit the owner's current work and audience, explains the ideas in plain language, and drafts posts around practical problems and approaches. A local dashboard manages information and progress; Telegram handles topic choices, feedback, and final approval.

```text
HN + GitHub + RSS → discovery and evidence → persona-led topic selection
    → owner perspective → source-grounded draft → review → exact approval → LinkedIn
```

Research and authoring use Gemini. Collection, scheduling, scoring, job recovery, and publication controls use ordinary Python services.

## Features and capabilities

- **Fresh discovery:** Hacker News stories, recently active GitHub repositories, and configurable RSS feeds, including LangChain, Hugging Face, Outcome School, OpenAI, Cloudflare, and engineering blogs. Collection runs every six hours while a collector is running, with manual refresh and visible job status.
- **Personal topic selection:** Uses career context, recent work, audience, interests, and rejection reasons. Returns one to five suitable suggestions with a simple explanation, reader takeaway, and source links. Personal fit and practical value carry more weight than popularity.
- **Practical writing:** New source-backed drafts first extract the problem, approach, and limitations. Writing preferences guide tone, length, technical depth, examples, emojis, and hashtags; writing samples guide voice without becoming evidence for personal claims.
- **Knowledge management:** Resume intake for PDF, DOCX, and text; manual facts; pasted profile text; source-backed fact suggestions; and guided questions. Suggested facts require confirmation and default to private. Download full private knowledge or a filtered public profile.
- **Review and history:** Choose or switch topics, request alternatives, revise drafts in ordinary language, restore versions from the current topic selection, and view past conversations. Switching topics preserves history while resetting input and invalidating old approvals.
- **Controlled publication:** LinkedIn OAuth, an exact final preview, and a one-use Telegram approval tied to the post version and account. Publishing is disabled by default. Uncertain publication results require review rather than automatic resend.

## Engineering highlights

- **Evidence survives generation:** Saved source snapshots support drafting and grounding checks; confirmed public facts are separated from private or unconfirmed knowledge.
- **Refresh has a dependency:** Topic generation waits for queued discovery. Regeneration linked to a failed refresh reports the failure rather than silently using older evidence.
- **Work survives restarts:** MongoDB-backed jobs use duplicate suppression, leases, and revision checks. Restarting a worker invalidates pending approvals.
- **Persona stays editable:** Each generation reads saved profile context rather than hardcoding the owner's resume into model instructions. Confidential terms are checked in suggestions, drafts, and publication controls.

Trend scores use recency, normalized engagement, and changes between observations. Topic ranking combines model-assessed personal fit (60%), practical value (25%), and trend signal (15%). These scores and grounding audits are heuristics; owner review remains essential.

## Tools and technology

| Area | Stack |
| --- | --- |
| Backend | Python 3.12–3.13, FastAPI, Uvicorn, Pydantic |
| Dashboard | React, TypeScript, Vite |
| Storage and background work | MongoDB/PyMongo, durable Python job worker |
| AI | Gemini through the Google Gen AI SDK |
| Discovery and extraction | HTTPX, Feedparser, Trafilatura, HN and GitHub APIs |
| Documents and credentials | PyPDF, python-docx, OS keyring |
| Owner interaction and publishing | Telegram Bot API, LinkedIn OAuth and Posts API |

## Setup

### 1. Install and configure

Install Python 3.12 or 3.13, `uv`, Node.js 22, and npm. Have a transaction-capable MongoDB deployment, such as Atlas or a replica set, a Gemini key, and a Telegram bot. LinkedIn connection/publishing requires a developer app with the necessary product access.

```sh
uv sync --locked
npm ci --prefix frontend
npm run build --prefix frontend
cp .env.example .env
```

Fill `.env` with MongoDB, Gemini, Telegram, and LinkedIn settings. Set a supported `GEMINI_MODEL` and `LINKEDIN_API_VERSION`; keep `PUBLISHING_ENABLED=false` for initial testing. `GITHUB_TOKEN` is optional and increases discovery API limits. Set `APP_TIMEZONE` to your local IANA timezone, for example `Asia/Kolkata`.

### 2. Configure local HTTPS

The dashboard uses `https://127.0.0.1:8765`. Create a local certificate:

```sh
mkdir -p .local-data/tls
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout .local-data/tls/localhost.key \
  -out .local-data/tls/localhost.crt \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
chmod 600 .local-data/tls/localhost.key
```

Trust the certificate using your OS certificate store (Keychain Access on macOS). Set `APP_TLS_CERT` and `APP_TLS_KEY` to these paths. Register this exact LinkedIn callback and use it for `LINKEDIN_REDIRECT_URI`:

```text
https://127.0.0.1:8765/api/v1/connections/linkedin/callback
```

### 3. Start the application

```sh
uv run linkedin-agent-foundation --apply
uv run linkedin-agent-api
```

Open the one-use dashboard link printed by the launcher. In the dashboard, pair your Telegram account, connect LinkedIn, and review your professional information, public facts, writing preferences, and confidential details. Start the worker in another terminal:

```sh
uv run linkedin-agent-worker
```

For discovery without Telegram, run `uv run linkedin-agent-discover` instead; it does not handle drafting or publication. Keep the machine and relevant processes running for background work. After frontend changes, rebuild the dashboard; after backend changes, restart the affected processes.

## Everyday workflow

1. Refresh discovery and wait for completion. Use **Find topics now**, or **Regenerate suggestions** when an unselected conversation is already open.
2. In Telegram, send `TOPICS`, then `CHOOSE <number>`. Refreshed lists use `CHOOSE <list revision>:<number>`. Add your perspective and indicate hands-on experience or exploration.
3. Send `DRAFT`, then give feedback in ordinary language. Use `CHOOSE` to switch topics before publication or `RESTORE <version>` for an eligible earlier draft.
4. Send `FINAL` to review the exact post. With publishing enabled, only the exact approval command shown in that preview authorizes publication. With publishing disabled, approval checks do not publish.

Use **Save topic preference** to explain mismatches such as “too advanced” or “too promotional.” Weekly invitations can be scheduled, paused, or resumed. `CONTINUE` recaps saved progress; `DISCARD` or `SKIP WEEK` closes a conversation.

## Verification and current scope

```sh
uv run python -m unittest discover -s tests -q
npm run build --prefix frontend
uv run linkedin-agent-diagnose --live-model
```

The diagnostic command checks provider connectivity and makes small synthetic Gemini requests. MongoDB integration tests are opt-in and require access to a disposable test database; the default suite skips them.

The app runs locally, but generation sends relevant profile context and source evidence to Gemini. Resume fact extraction sends the selected source text when explicitly requested. Keep credentials, certificates, uploads, and private exports out of Git.

Current scope is a single owner and text posts. Semantic topic clustering, Reddit/search adapters, Firecrawl fallback, GitHub profile knowledge intake, and knowledge re-import are deferred. Source failures can reduce coverage, and generated writing still needs human review.

For implementation details, see the [system design](docs/SystemDesign_Final.md), [knowledge portability contract](docs/knowledge-portability.md), [publishing and recovery](docs/phase-5-status.md), and [pause, resume, and history](docs/phase-6-status.md). Discovery sources and collection limits are editable in [research-sources.json](config/research-sources.json).
