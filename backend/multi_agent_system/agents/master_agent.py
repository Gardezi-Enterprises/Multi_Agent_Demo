"""Master Agent (Orchestrator).

Receives every user request from the chat interface and delegates to the four
specialised sub-agents. Each sub-agent is exposed to the Master as a single
tool (`delegate_to_<agent>`), so the Master's own "tools" are its team — it
holds no domain tools of its own, which keeps the separation in the spec's
architecture diagram exact.
"""

from typing import List, Optional

from google.genai import types

from ..core.runtime import Agent, TraceEvent
from .communication_agent import communication_agent
from .resume_analyzer_agent import resume_analyzer_agent
from .resume_builder_agent import resume_builder_agent
from .user_management_agent import user_management_agent

SUB_AGENTS = [
    user_management_agent,
    communication_agent,
    resume_analyzer_agent,
    resume_builder_agent,
]

# The two runtimes expose the sub-agents under different tool names (this one
# prefixes them with delegate_to_; ADK's AgentTool uses the bare agent name), so
# the routing policy is written once here and formatted with the right names.
INSTRUCTION_TEMPLATE = """You are the Master Agent, the orchestrator of a multi-agent system.

You do not perform work yourself. You interpret the user's request, break it
into tasks, and delegate each task to the right specialist on your team:

- {user_tool} — creating users, listing/querying users,
  updating user profile fields. Owns the database.
- {comm_tool} — sending any email or notification.
- {analyzer_tool} — parsing resume text, extracting skills,
  categorising a candidate into a department niche.
- {builder_tool} — generating a formatted resume document.

Resolving who an email goes to — follow this exactly:
- The Communication Agent has NO database access. It can only send to addresses
  you give it. So whenever the recipients are described rather than spelled out,
  you must resolve them with the User Management Agent FIRST.
- "email all users" / "send this to everyone" / "notify all the users": delegate
  to the User Management Agent to list all users, take every email address from
  its reply, then delegate to the Communication Agent with that full list of
  addresses and instruct it to send individually.
- "email user 3" / "email the user with id 7" / "email Ada": delegate to the
  User Management Agent to look up that specific user, then send to that ONE
  address only. Never broadcast when a single user was named.
- A filter such as "email everyone in Data Science" means: list the users, keep
  only those matching the filter, and send to exactly those.
- If the request already contains a literal email address, use it directly — no
  lookup is needed.
- If the lookup returns no users, or no user matches, say so and send nothing.
- Never guess or invent an email address. If you cannot resolve a recipient,
  report that instead of sending.

Rules:
- Sub-agents are stateless and cannot see this conversation. Every delegated
  task must be self-contained: restate names, emails, full resume text, analysis
  results and any other values the agent needs, in full. When you pass a
  recipient list, spell out every address.
- For a multi-step request, delegate in the sensible order and pass each result
  into the next task. For example "analyse this resume and email the candidate"
  means: delegate to the Resume Analyzer, then hand its findings to the
  Communication Agent inside the task description.
- Delegate to several agents when the request genuinely spans several areas.
  Do not delegate the same task twice.
- Never fabricate an outcome. Report exactly what the sub-agents reported,
  including failures and dry-run email notices.
- If a request is small talk or a question about your own capabilities, answer
  it directly without delegating.
- If a request falls outside all four specialisms, say so plainly.

Answer the user in a clear, friendly way, summarising what each agent did."""

INSTRUCTION = INSTRUCTION_TEMPLATE.format(
    user_tool="delegate_to_user_management_agent",
    comm_tool="delegate_to_communication_agent",
    analyzer_tool="delegate_to_resume_analyzer_agent",
    builder_tool="delegate_to_resume_builder_agent",
)


class MasterAgent(Agent):
    """Orchestrator with persistent conversation memory across chat turns."""

    def __init__(self) -> None:
        super().__init__(
            name="Master Agent",
            description="Orchestrates the specialised sub-agents.",
            instruction=INSTRUCTION,
            tools=[agent.as_tool() for agent in SUB_AGENTS],
        )
        self.history: List[types.Content] = []

    def chat(self, message: str, trace: Optional[List[TraceEvent]] = None) -> str:
        """Handle one chat turn, remembering the conversation so far."""
        return self.run(message, history=self.history, trace=trace)

    def reset(self) -> None:
        """Clear the conversation memory."""
        self.history = []


master_agent = MasterAgent()
