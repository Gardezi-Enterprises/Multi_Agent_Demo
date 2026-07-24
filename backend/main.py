"""CLI chat interface for the multi-agent system.

Usage:
    python main.py                 interactive chat
    python main.py "your request"  single request, then exit
    python main.py --quiet         hide the delegation trace
"""

import sys

# The trace and banner use box-drawing characters; the default Windows console
# codepage (cp1252) cannot encode them, so force UTF-8 before anything prints.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - non-reconfigurable stream
            pass

from multi_agent_system import commands
from multi_agent_system.agents import master_agent
from multi_agent_system.config import GEMINI_MODEL
from multi_agent_system.core.runtime import TraceEvent
from multi_agent_system.db.database import init_db

BANNER = f"""
╭──────────────────────────────────────────────────────────────╮
│  Taseer's Agent — Master Orchestrator + 4 specialists        │
│  Built on the Google Gen AI SDK                              │
╰──────────────────────────────────────────────────────────────╯
 model: {GEMINI_MODEL}
 team : User Management · Communication · Resume Analyzer · Resume Builder

 Try:
   • add a user named Ada Lovelace with email ada@example.com
   • list all users
   • analyse this resume: <paste resume text>
   • build a resume for Ravi Kumar, a DevOps engineer with 7 years experience
   • email ada@example.com telling her the interview is on Friday

 Slash commands: {"  ".join("/" + c.name for c in commands.COMMANDS)}
 Session:        /reset  clear memory   ·   /exit  quit
"""


def show_trace(trace: list) -> None:
    if not trace:
        return
    print("\n\033[90m── delegation trace " + "─" * 42)
    for event in trace:
        if event.kind != "final":
            print("\033[90m" + event.render())
    print("\033[90m" + "─" * 61 + "\033[0m")


def handle(message: str, quiet: bool) -> None:
    trace: list[TraceEvent] = []
    try:
        answer = master_agent.chat(commands.expand(message), trace=trace)
    except Exception as exc:
        print(f"\n\033[91mError: {type(exc).__name__}: {exc}\033[0m")
        return
    if not quiet:
        show_trace(trace)
    print(f"\n\033[96mMaster Agent\033[0m: {answer}\n")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    quiet = "--quiet" in args
    args = [a for a in args if a != "--quiet"]

    init_db()

    if args:
        handle(" ".join(args), quiet)
        return

    print(BANNER)
    while True:
        try:
            message = input("\033[92mYou\033[0m: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if not message:
            continue
        if message.lower() in ("/exit", "/quit", "exit", "quit"):
            print("Bye.")
            return
        if message.lower() == "/reset":
            master_agent.reset()
            print("Conversation memory cleared.\n")
            continue
        handle(message, quiet)


if __name__ == "__main__":
    main()
