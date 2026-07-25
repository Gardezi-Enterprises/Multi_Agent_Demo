# Taseer's Agent — Multi-Agent System

A Master Agent (Orchestrator) receives requests from a chat interface and delegates
them to four specialised sub-agents, each owning its own Python tools — the
architecture described in [multi_agent_system.md](multi_agent_system.md).

Built on the **Google Gen AI SDK**, following Google Agent Development Kit
orchestration patterns: a Master agent whose tools are its sub-agents, each
sub-agent owning typed Python tools, driven by a function-calling loop.

- **Backend** — FastAPI serving a JSON/SSE API (`backend/`)
- **Frontend** — React + Vite + TypeScript single-page app (`frontend/`)
- **Same-origin** — in production the backend serves the built SPA; in dev, Vite
  proxies `/api`. Cookie auth, no CORS.
- **SQLite or Postgres** — zero-config SQLite locally; set `DATABASE_URL` to a free
  managed Postgres (Neon/Supabase) for a fully-online deploy. Same code, both.

---

## Architecture

```
                       ┌─────────────────────────┐
                       │  React SPA (chat UI)    │
                       └────────────┬────────────┘
                                    │  /api  (JSON + SSE)
                       ┌────────────▼────────────┐
                       │   FastAPI  ·  auth,     │
                       │   sessions, streaming   │
                       └────────────┬────────────┘
                       ┌────────────▼────────────┐
                       │      Master Agent       │
                       │     (Orchestrator)      │
                       └────────────┬────────────┘
        ┌───────────────────┬───────┴───────────┬───────────────────┐
        ▼                   ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  User Mgmt    │   │ Communication │   │Resume Analyzer│   │ Resume Builder│
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
   [DB Tools]         [Email Tools]     [Analysis Tools]     [Doc Gen Tools]
```

| Sub-Agent | Responsibility | Tools |
| :--- | :--- | :--- |
| **User Management Agent** | CRUD on users, querying, updating profile fields | `create_user`, `get_all_users`, `edit_user` |
| **Communication Agent** | Sending emails and notifications (one user or all) | `send_email` |
| **Resume Analyzer Agent** | Parsing resume text, extracting skills, categorising | `analyze_resume_text` |
| **Resume Builder Agent** | Generating formatted professional resumes | `generate_resume_document` |

The Master holds **no domain tools of its own** — its only tools are its four
sub-agents (the agent-as-tool pattern).

---

## Project layout

```
backend/
  app.py                 FastAPI: routes, SSE streaming, static SPA serving
  serving.py             transport-agnostic helpers (trace, rate limit, files)
  main.py                CLI chat client
  requirements.txt
  .env / .env.example
  multi_agent_system/    the agent system — reused unchanged by API and CLI
    config.py  logging_config.py  sessions.py  accounts.py  auth.py
    documents.py  email_providers.py  commands.py
    core/runtime.py      Agent class: function-calling loop, delegation, tracing
    agents/              master + 4 sub-agents
    tools/               db, email, analysis, docgen tools
    db/database.py       SQLite (users + auth_accounts)
  tests/                 test_tools · test_runtime · test_accounts · test_api
  static/                built SPA (generated; gitignored)
frontend/
  src/
    App.tsx  main.tsx  api.ts  markdown.ts  theme.ts  theme.css
    pages/       Login · Signup · Chat
    components/  TreeLogo · Toasts · AccountModal
  vite.config.ts         dev proxy /api → :8000; build → ../backend/static
Dockerfile               multi-stage: node build → python runtime
render.yaml · fly.toml   deployment blueprints
```

---

## Run it locally

**1. Backend** (Python 3.10+):

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # add GOOGLE_API_KEY and an admin password hash
python -m uvicorn app:app --reload --port 8000
```

Get a Gemini key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
Generate an admin password hash with `python -m multi_agent_system.auth`.

**2. Frontend** (Node 18+):

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

**Or run as one server** (production shape):

```bash
cd frontend && npm run build   # emits into backend/static/
cd ../backend && python -m uvicorn app:app --port 8000
# open http://localhost:8000
```

**CLI** (no browser):

```bash
cd backend && python main.py "list all users"
```

**Tests** (no API key needed):

```bash
cd backend
python tests/test_tools.py          # 41 — tool layer
python tests/test_runtime.py        # 36 — sessions, config, serving helpers, auth
python tests/test_accounts.py       # 13 — account store lifecycle
python tests/test_conversations.py  #  9 — per-user chat history isolation
python tests/test_api.py            # 17 — routes, gating, SSE, ownership
```

---

## Access control

Password-protected. Every page and API route requires a session except
`/api/health` (liveness for probes) and the auth endpoints.

- **Signup** — the first account bootstraps the **owner (admin)**. After that,
  signup is closed unless `AUTH_ALLOW_SIGNUP=true` (optionally behind
  `AUTH_SIGNUP_CODE`). Open public signup would hand full access to any visitor,
  so it is off by default.
- **Admin panel** — admins add and remove accounts from the account menu.
- **Change password** — from the account menu; invalidates that user's other
  sessions (the token signature is bound to the password hash).

```ini
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD_HASH=scrypt$...     # python -m multi_agent_system.auth
SECRET_KEY=<32+ random bytes>     # signs login cookies; required in production
COOKIE_SECURE=true                # over HTTPS
```

**Fails closed:** with `ENVIRONMENT=production` and `AUTH_ENABLED=false`, the
server refuses to start. Passwords are salted **scrypt**; sessions are stateless
signed tokens; failed logins lock an IP out; a wrong username costs the same time
as a wrong password.

---

## Email

`send_email` delivers via a pluggable provider (`smtp`, `resend`, `sendgrid`,
`brevo`) or runs in **dry-run** (`EMAIL_ENABLED=false`) — composed and logged to
`output/sent_emails.log`, not delivered.

```ini
EMAIL_ENABLED=true
EMAIL_PROVIDER=smtp
EMAIL_FROM=you@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465                     # 465 = implicit TLS, 587 = STARTTLS
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password   # Gmail needs an App Password
```

"Email everyone" resolves recipients through the User Management Agent first
(the Communication Agent has no database access), then sends each recipient their
own copy. For large broadcasts prefer an HTTP provider — higher limits and real
bounce reporting than SMTP.

---

## Design notes

**The package is transport-agnostic.** `multi_agent_system/` knows nothing about
HTTP — the same code backs the API, the CLI and the tests. The FastAPI layer only
does auth, sessions, streaming and file transfer.

**Streaming.** `/api/chat/stream` runs the synchronous agent turn in a worker
thread; its trace list pushes each delegation and tool call to the event loop
thread-safely, and `StreamingResponse` emits SSE frames the browser renders live.

**Per-user chat history.** Conversations live in the database (`conversations`
and `messages`), owned by the account. Every query is scoped to the signed-in
owner, so a user only ever sees their own chats — even an admin gets a 404 for
someone else's conversation id. History persists across logout/login and
devices; nothing is kept in the browser. Each turn rebuilds the agent's context
from the stored conversation, so concurrent users never share memory.

**Generic, not hardcoded.** `analyze_resume_text` has no fixed skill or department
list — it categorises any profession (a cardiologist as "Cardiology", a chef as
"Culinary Arts"). `generate_resume_document` accepts arbitrary sections;
`send_email` invents no signature.

**Honest failure.** Tools return `{status: ...}` dicts; a `status: error` or
`partial` is reported as such, never rounded up to success.

**Gemini 3 thought signatures** are preserved across tool round-trips.

**Free-tier rate limits.** Per-minute limits are retried with the server's
backoff; a per-day exhaustion rotates to `GOOGLE_API_KEY_2` if set. The default
model is `gemini-3.1-flash-lite` (the `gemini-3.6-flash` free tier is only 20
requests/day).

---

## Deployment

**Want it live for free?** See **[DEPLOY_FREE.md](DEPLOY_FREE.md)** — a
free host (Hugging Face Spaces / Koyeb / Render) + a free Neon Postgres, with your
data persisted and no local machine involved.

### Not Vercel (for the backend)

Vercel is serverless: agent turns run 5–60s+, sessions live in memory, SQLite and
generated files need a writable disk, and SSE holds a connection open — all of
which need a persistent container. The SPA is served by the backend (same origin)
by design, so there is nothing to split off to Vercel.

### Recommended: Google Cloud Run

```bash
gcloud run deploy taseers-agent --source . --region us-central1 \
  --min-instances 1 --max-instances 1 --timeout 900 \
  --set-env-vars ENVIRONMENT=production,LOG_FORMAT=json,AUTH_ENABLED=true,COOKIE_SECURE=true \
  --set-secrets GOOGLE_API_KEY=google-api-key:latest,AUTH_PASSWORD_HASH=admin-hash:latest,SECRET_KEY=secret-key:latest
```

`--max-instances 1` while sessions and SQLite are per-process. To scale out, move
sessions to Redis and SQLite to Cloud SQL. Mount a volume (or GCS) for `data/` if
generated documents must persist.

### Also good

| Host | Notes |
| :--- | :--- |
| **Render** | `render.yaml` included; persistent disk at `/app/data`; generates `SECRET_KEY` |
| **Fly.io** | `fly.toml` included; `pristine = true` so SSE isn't buffered |
| **Railway / any VPS** | Detects the Dockerfile; add a volume for `/app/data` |

### Locally with Docker

```bash
docker build -t taseers-agent .
docker run -p 8000:8000 --env-file backend/.env -e ENVIRONMENT=staging taseers-agent
```

### Before going live

1. **Rotate** the API keys and Gmail app password shared in plain text.
2. Set real secrets in the host's secret manager; never commit `.env`.
3. `ENVIRONMENT=production`, `AUTH_ENABLED=true`, `COOKIE_SECURE=true`, a stable
   `SECRET_KEY`, and a strong `AUTH_PASSWORD_HASH`.
4. Point `DATABASE_URL`, `OUTPUT_DIR`, `UPLOAD_DIR` at a mounted volume.
5. Watch `/api/health`.
