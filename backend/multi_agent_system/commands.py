"""Slash commands — explicit routing hints for the Master Agent.

Typing `/email bob@x.com the interview is Friday` is shorter and less ambiguous
than a sentence, and it tells the orchestrator exactly which specialist to use.
A command expands into a fully-formed instruction before it reaches the Master
Agent, so the agent layer needs no knowledge of the syntax and the same commands
work identically in the web UI and the CLI.

Anything not starting with `/` is passed through untouched — commands are a
shortcut, never a requirement.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Command:
    name: str
    agent: str
    summary: str
    usage: str
    example: str
    template: str  # {args} is replaced with whatever followed the command
    needs_args: bool = True

    def expand(self, args: str) -> str:
        return self.template.format(args=args.strip())


COMMANDS: List[Command] = [
    Command(
        name="users",
        agent="User Management Agent",
        summary="List every user in the database",
        usage="/users",
        example="/users",
        template="List all users currently in the database. {args}",
        needs_args=False,
    ),
    Command(
        name="create",
        agent="User Management Agent",
        summary="Create a new user record",
        usage="/create <name>, <email>, [department], [skills]",
        example="/create Ada Lovelace, ada@example.com, Data Science",
        template=(
            "Create a new user from these details: {args}. "
            "Map each value to the correct field, and report the created record."
        ),
    ),
    Command(
        name="edit",
        agent="User Management Agent",
        summary="Update fields on an existing user",
        usage="/edit <who> -> <what to change>",
        example="/edit ada@example.com -> department to Engineering",
        template=(
            "Update an existing user. Identify the user and apply the changes "
            "described here: {args}. Look the user up first if needed."
        ),
    ),
    Command(
        name="email",
        agent="Communication Agent",
        summary="Compose and send an email",
        usage="/email <recipient> <what to say>",
        example="/email ada@example.com invite her to interview on Friday 10am",
        template=(
            "Send an email. The recipient and the intent are: {args}. "
            "Write an appropriate subject line and a professional body."
        ),
    ),
    Command(
        name="broadcast",
        agent="User Management Agent + Communication Agent",
        summary="Email every user in the database",
        usage="/broadcast <what to say>  (add 'to <department>' to filter)",
        example="/broadcast the office will be closed on Monday for maintenance",
        template=(
            "Send an email to ALL users in the database. First list every user to "
            "collect their email addresses, then send the email to that full list "
            "individually, so no recipient sees the others. If a department or "
            "other filter is mentioned, send only to the users matching it. "
            "Report how many recipients received it. The message is: {args}"
        ),
    ),
    Command(
        name="notify",
        agent="User Management Agent + Communication Agent",
        summary="Email one specific user by id, name or email",
        usage="/notify <user id, name or email> -> <what to say>",
        example="/notify 3 -> your interview is confirmed for Friday at 10am",
        template=(
            "Send an email to exactly ONE user, identified here: {args}. "
            "Look the user up in the database first to get their email address if "
            "an address was not given. Send to that single address only — this is "
            "not a broadcast. If the user cannot be found, say so and send nothing."
        ),
    ),
    Command(
        name="analyze",
        agent="Resume Analyzer Agent",
        summary="Analyse resume text and categorise the candidate",
        usage="/analyze <resume text, or attach a file>",
        example="/analyze Sam Lee, sam@x.com. 5 yrs. Docker, Kubernetes, AWS.",
        template=(
            "Analyse this resume: extract the skills and categorise the "
            "candidate into a department niche. Resume:\n{args}"
        ),
    ),
    Command(
        name="build",
        agent="Resume Builder Agent",
        summary="Generate a formatted resume document",
        usage="/build <candidate details>",
        example="/build Ravi Kumar, DevOps engineer, ravi@x.com, Python, AWS, Docker",
        template=(
            "Build a formatted professional resume document from these "
            "details: {args}. Use only the facts given."
        ),
    ),
    Command(
        name="screen",
        agent="Resume Analyzer Agent + User Management Agent",
        summary="Analyse a resume, then save the candidate as a user",
        usage="/screen <resume text, or attach a file>",
        example="/screen Sam Lee, sam@x.com. 5 yrs. Docker, Kubernetes, AWS.",
        template=(
            "First analyse this resume to extract the candidate's skills and "
            "department niche, then create a user record for that candidate "
            "using the extracted name, email, department and skills. "
            "Report both steps. Resume:\n{args}"
        ),
    ),
    Command(
        name="agents",
        agent="Master Agent",
        summary="Describe the team and what each agent can do",
        usage="/agents",
        example="/agents",
        template=(
            "Describe your team: list each sub-agent you can delegate to, what "
            "it is responsible for, and which tools it owns. Do not call any "
            "tools to answer this. {args}"
        ),
        needs_args=False,
    ),
]

BY_NAME: Dict[str, Command] = {c.name: c for c in COMMANDS}


def find(name: str) -> Optional[Command]:
    return BY_NAME.get(name.lower().lstrip("/"))


def expand(message: str) -> str:
    """Expand a leading slash command into a full instruction.

    Returns the message unchanged when it is not a command, so ordinary chat
    and commands share one path.
    """
    text = (message or "").strip()
    if not text.startswith("/"):
        return text

    head, _, rest = text.partition(" ")
    command = find(head)
    if command is None:
        known = ", ".join("/" + c.name for c in COMMANDS)
        return (
            f"The user typed an unrecognised command '{head}'. Tell them it is "
            f"not a known command and that the available commands are: {known}. "
            "Do not call any tools."
        )
    if command.needs_args and not rest.strip():
        return (
            f"The user typed '{head}' with no details. Ask them for what is "
            f"missing. The correct usage is: {command.usage}. Do not call any tools."
        )
    return command.expand(rest)


def as_json() -> List[dict]:
    """Command metadata for the UI autocomplete and the guide panel."""
    return [
        {
            "name": c.name,
            "agent": c.agent,
            "summary": c.summary,
            "usage": c.usage,
            "example": c.example,
            "needs_args": c.needs_args,
        }
        for c in COMMANDS
    ]
