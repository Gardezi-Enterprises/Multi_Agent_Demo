# Submission Report — Agentic Multi-Agent System

**Project:** Agentic Multi-Agent System using Google Gen AI SDK (with Google ADK orchestration patterns)
**Repository:** https://github.com/Gardezi-Enterprises/Multi_Agent_Demo
**Live deployment:** https://taseers-agent.onrender.com
**Specification followed:** [multi_agent_system.md](multi_agent_system.md)

---

## 1. Summary

I built a working multi-agent system exactly as described in the specification: a
**Master Agent (Orchestrator)** that receives requests from a **chat interface**
and delegates them to **four specialised sub-agents**, each owning the specific
Python tools named in the spec. It is implemented on the **Google Gen AI SDK**
(`google-genai`), following the **Google Agent Development Kit orchestration
pattern** (a master whose tools are its sub-agents — the agent-as-tool pattern).

Every agent, every tool, and every responsibility in the specification's team
table is present and wired precisely as written. On top of the required core I
added production engineering — a React chat UI, authentication, per-user chat
history, a database layer, streaming, a full test suite, and a live free
deployment.

---

## 2. Requirement compliance

### 2.1 Headline requirements

| Requirement (from the spec) | How I fulfilled it | Evidence |
| :--- | :--- | :--- |
| Built on the **Google Gen AI SDK** | The agent runtime drives Gemini function-calling with `google.genai` | `backend/multi_agent_system/core/runtime.py` |
| Uses **Google ADK patterns** | Master orchestrator + sub-agents exposed as callable tools (agent-as-tool), a hand-implemented ADK pattern | `backend/multi_agent_system/agents/master_agent.py` |
| A **Master Agent (Orchestrator)** | `MasterAgent` holds **no domain tools of its own** — its only tools are the four sub-agents; it interprets each request and delegates | `master_agent.py` (`SUB_AGENTS`, `INSTRUCTION_TEMPLATE`) |
| Receives requests from a **chat interface** | A React single-page chat app; the Master receives each message and orchestrates | `frontend/src/pages/Chat.tsx` |
| Delegates to **four specialised sub-agents** | Four `Agent` instances, each with a narrow instruction and only its own tools | `backend/multi_agent_system/agents/` |
| Each sub-agent has **specific Python tools** | The tools are plain typed Python functions; the SDK derives the function-calling schema from their signatures + docstrings | `backend/multi_agent_system/tools/` |

### 2.2 Team division & responsibilities — exact match to the spec table

| Sub-Agent | Spec responsibility | Spec tools | Implemented tools | Match |
| :--- | :--- | :--- | :--- | :---: |
| **User Management Agent** | CRUD on users, querying user lists, updating profile fields | `create_user`, `get_all_users`, `edit_user` | `create_user`, `get_all_users`, `edit_user` | ✅ |
| **Communication Agent** | Sending emails and dispatching notifications | `send_email` | `send_email` | ✅ |
| **Resume Analyzer Agent** | Parsing resume text, extracting skills, categorising into department niches | `analyze_resume_text` | `analyze_resume_text` | ✅ |
| **Resume Builder Agent** | Generating formatted professional resumes from user details | `generate_resume_document` | `generate_resume_document` | ✅ |

The tool assignments are defined here:

- `agents/user_management_agent.py` → `tools=[create_user, get_all_users, edit_user]`
- `agents/communication_agent.py` → `tools=[send_email]`
- `agents/resume_analyzer_agent.py` → `tools=[analyze_resume_text]`
- `agents/resume_builder_agent.py` → `tools=[generate_resume_document]`

### 2.3 Architecture — matches the spec diagram

```
        User / Chat Interface  (React SPA)
                  │
           Master Agent (Orchestrator)
                  │  delegates to
   ┌──────────────┼──────────────┬──────────────┐
   ▼              ▼              ▼              ▼
User Mgmt    Communication   Resume Analyzer  Resume Builder
[DB Tools]   [Email Tools]   [Analysis Tools] [Doc Gen Tools]
```

This is the exact flow drawn in the specification, implemented end to end.

---

## 3. How each sub-agent works (against its brief)

**User Management Agent — CRUD + querying + profile updates.**
`create_user` validates and inserts a user (unique email enforced), `get_all_users`
lists them, `edit_user` updates profile fields by id or email. Tools return a
`{status: ...}` result so failures are reported honestly, never faked.

**Communication Agent — email & notifications.**
`send_email` composes and sends mail, supporting one recipient or a whole list
(broadcast). It has no database access by design, so "email all users" is
resolved by the Master (list users → send), preserving the separation of duties.

**Resume Analyzer Agent — parse, extract skills, categorise into department niches.**
`analyze_resume_text` extracts skills and assigns a department niche. It is
domain-agnostic: it categorises any profession (e.g. a cardiologist → "Cardiology",
a chef → "Culinary Arts"), not just technology roles, exactly satisfying
"categorising into department niches."

**Resume Builder Agent — generate formatted professional resumes.**
`generate_resume_document` produces a formatted `.docx` (or `.txt`) from the
candidate's details, downloadable from the chat.

---

## 4. The orchestrator and the Gen AI SDK

- The Master and sub-agents are all `Agent` objects built on a hand-written
  **function-calling loop** over `google.genai` (`core/runtime.py`). Automatic
  function calling is intentionally disabled so every tool call and delegation is
  observable and traceable.
- Each sub-agent is wrapped as a single callable tool (`as_tool()` /
  `delegate_to_<agent>`), which the Master invokes — this is the ADK
  "agent-as-tool" orchestration pattern the spec references.
- Gemini 3 **thought signatures** are preserved across tool round-trips, so
  multi-step tool use is correct.
- Multi-step requests work: e.g. "analyse this resume then create a user for the
  candidate" runs Analyzer → User Management in one turn, visible in the
  delegation trace.

---

## 5. Engineering added on top of the specification

These were not required by the spec but make the system a complete, deployable
product:

- **Chat interface (React + Vite + TypeScript):** modern UI with a live
  delegation trace, slash commands, file upload, and document download.
- **Authentication:** signup, login, logout, change password, emailed password
  reset, admin account management; scrypt password hashing and signed session
  tokens.
- **Per-user chat history:** conversations are stored server-side and scoped to
  the owner, so no user can see another's history.
- **Database layer:** SQLite for local development and **PostgreSQL** for
  production (selected by `DATABASE_URL`), with connection pooling.
- **Streaming:** the chat streams each delegation and tool call over
  Server-Sent Events so the user sees the orchestration happen live.
- **Pluggable email providers:** SMTP, Resend, SendGrid, Brevo, or a dry-run
  mode — so email works across hosting environments.
- **CLI:** the same agent system is usable from a command line (`python main.py`).

---

## 6. Testing & verification

The system is covered by an automated test suite that runs without an API key:

| Suite | Count | What it verifies |
| :--- | :---: | :--- |
| `test_tools.py` | 41 | the six tools (create/get/edit user, send_email, analyze, build) |
| `test_runtime.py` | 36 | sessions, config validation, serving helpers, auth primitives |
| `test_accounts.py` | 13 | account store lifecycle (hashing, tokens, admin rules) |
| `test_conversations.py` | 9 | per-user chat history isolation |
| `test_api.py` | 17 | FastAPI routes, auth gating, the SSE contract |
| `test_postgres.py` | 3 | the same store verified on real PostgreSQL |
| **Total** | **119** | |

I also verified the orchestration live: each sub-agent runs against the real
Gemini API, chained multi-agent requests complete, resumes generate and download,
and email delivers through an HTTP provider.

---

## 7. Deployment

The application is deployed and publicly accessible:

- **Host:** Render (free web service, Docker) — https://taseers-agent.onrender.com
- **Database:** Neon (free managed PostgreSQL) — data persists independently of
  the host.
- **Email:** Brevo HTTP API (verified sender, free tier).
- **Availability:** a scheduled GitHub Action pings the health endpoint to avoid
  cold starts.

Because the frontend is served by the backend (same origin) and the database is a
managed cloud Postgres, the whole system runs online with no dependency on any
local machine, and new users can register from anywhere.

---

## 8. Repository structure

```
backend/                     FastAPI app + the agent system
  app.py                     HTTP/SSE API, auth, static SPA serving
  multi_agent_system/
    core/runtime.py          Agent runtime (Gen AI SDK function-calling loop)
    agents/                  master_agent + the four sub-agents
    tools/                   db_tools, email_tools, analysis_tools, docgen_tools
    db/database.py           SQLite/PostgreSQL layer
    accounts.py, auth.py, conversations.py, email_providers.py, commands.py
  tests/                     119 automated tests
  main.py                    CLI client
frontend/                    React + Vite + TypeScript chat UI
Dockerfile                   multi-stage build (React → FastAPI image)
render.yaml, DEPLOY_FREE.md  deployment configuration and guide
multi_agent_system.md        the original specification
```

---

## 9. Conclusion

The delivered system fulfils the specification in full: a Google Gen AI SDK
multi-agent system with a Master Orchestrator delegating to the four named
sub-agents, each owning exactly the tools listed in the team-division table,
receiving requests from a chat interface. Beyond the required scope I made it a
complete, tested, and publicly deployed application. Every point in
`multi_agent_system.md` is implemented and verifiable in the repository and the
live deployment.
