# Agentic Multi-Agent System using Google ADK / Gen AI SDK

This project demonstrates a multi-agent system implemented using the **Google Gen AI SDK** (and Google Agent Development Kit patterns). A **Master Agent (Orchestrator)** receives user requests from a chat interface and delegates tasks to four specialized sub-agents with specific python tools.

---

## 🏗️ Architecture Overview

```
                       ┌─────────────────────────┐
                       │  User / Chat Interface  │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │      Master Agent       │
                       │     (Orchestrator)      │
                       └────────────┬────────────┘
                                    │
        ┌───────────────────┬───────┴───────────┬───────────────────┐
        ▼                   ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  User Mgmt    │   │  Communication│   │ Resume Analyzer│   │ Resume Builder│
│    Agent      │   │     Agent     │   │     Agent     │   │     Agent     │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │                   │
  [DB Tools]          [Email Tools]     [Analysis Tools]     [Doc Gen Tools]
```

### Team Division & Responsibilities

| Sub-Agent | Scope / Responsibility | Assigned Tools |
| :--- | :--- | :--- |
| **User Management Agent** | CRUD operations on users, querying user lists, updating profile fields | `create_user`, `get_all_users`, `edit_user` |
| **Communication Agent** | Sending emails and dispatching notifications | `send_email` |
| **Resume Analyzer Agent** | Parsing resume text, extracting skills, and categorizing into department niches | `analyze_resume_text` |
| **Resume Builder Agent** | Generating formatted professional resumes based on user details | `generate_resume_document` |

---

