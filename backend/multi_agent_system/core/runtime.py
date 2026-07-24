"""The agent runtime, implemented directly on the Google Gen AI SDK.

This module hand-implements the Agent Development Kit pattern — an agent is a
model + instruction + a set of Python tools, driven by a function-calling loop —
using only `google.genai`. Automatic function calling is deliberately disabled
so that every tool invocation passes through `Agent._invoke`, which is what
makes delegation traceable and lets sub-agents be exposed to the Master Agent
as tools (the agent-as-tool pattern).

Note on Gemini 3 models: the model's own `Content` (including any
`thought_signature` on its parts) must be appended to the history verbatim
before the function response is sent back, otherwise multi-step tool use breaks.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

from ..config import API_KEYS, GEMINI_MODEL

MAX_TOOL_ITERATIONS = 8

# A single orchestrated turn costs several model calls (Master + sub-agent, each
# possibly looping over tools). Free-tier keys are rate limited per minute, so
# transient 429/503 responses are expected and retried rather than surfaced.
MAX_RETRIES = 4
RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+)s'")

_client: Optional[genai.Client] = None
_key_index = 0


def slugify(name: str) -> str:
    """Turn an agent's display name into a valid function-declaration name."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()


def rotate_api_key() -> bool:
    """Switch to the next configured API key. Returns False if none is left.

    Only called when the current key's daily quota is exhausted, so fallback
    keys are never touched while the primary key still has allowance.
    """
    global _client, _key_index
    if _key_index + 1 >= len(API_KEYS):
        return False
    _key_index += 1
    _client = genai.Client(api_key=API_KEYS[_key_index])
    return True


def get_client() -> genai.Client:
    """Return the shared Gen AI client, created on first use."""
    global _client
    if _client is None:
        if not API_KEYS:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        _client = genai.Client(api_key=API_KEYS[_key_index])
    return _client


@dataclass
class TraceEvent:
    """One observable step: a tool call, a delegation, or a final answer."""

    agent: str
    kind: str  # "tool_call" | "delegation" | "final"
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None

    def render(self) -> str:
        if self.kind == "delegation":
            return f"  ↳ {self.agent} delegates to {self.name}"
        if self.kind == "tool_call":
            shown = {k: v for k, v in self.args.items() if v not in (None, "", [])}
            preview = ", ".join(f"{k}={json.dumps(v, default=str)[:40]}" for k, v in shown.items())
            status = (self.result or {}).get("status", "?") if isinstance(self.result, dict) else "?"
            return f"     • {self.agent} → {self.name}({preview}) [{status}]"
        return f"  ↳ {self.agent} responded"


class Agent:
    """An LLM agent with a fixed instruction and a set of callable tools."""

    def __init__(
        self,
        name: str,
        description: str,
        instruction: str,
        tools: Optional[List[Callable]] = None,
        model: Optional[str] = None,
    ) -> None:
        self.name = name
        self.description = description
        self.instruction = instruction
        self.tools: Dict[str, Callable] = {fn.__name__: fn for fn in (tools or [])}
        self.model = model or GEMINI_MODEL
        self._declarations: Optional[List[types.FunctionDeclaration]] = None

    # -- configuration --------------------------------------------------------

    def _function_declarations(self) -> List[types.FunctionDeclaration]:
        """Derive tool schemas from the Python signatures + docstrings, once."""
        if self._declarations is None:
            client = get_client()
            self._declarations = [
                types.FunctionDeclaration.from_callable(client=client, callable=fn)
                for fn in self.tools.values()
            ]
        return self._declarations

    def _config(self) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(
            system_instruction=self.instruction,
            temperature=0.2,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self.tools:
            config.tools = [types.Tool(function_declarations=self._function_declarations())]
        return config

    # -- execution ------------------------------------------------------------

    def _generate(self, contents: List[types.Content], config: types.GenerateContentConfig):
        """Call the model, retrying transient rate-limit / overload errors.

        Honours the server-supplied retryDelay when present, otherwise backs off
        exponentially. Non-transient errors propagate immediately.
        """
        client = get_client()
        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                return client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
            except Exception as exc:
                message = str(exc)
                # A per-DAY quota will not recover within the retry window. Try
                # a fallback key if one is configured; otherwise give up now
                # rather than sleeping pointlessly.
                daily_quota = "PerDay" in message
                if daily_quota and rotate_api_key():
                    continue
                transient = not daily_quota and (
                    ("429" in message and "RESOURCE_EXHAUSTED" in message)
                    or ("503" in message and "UNAVAILABLE" in message)
                )
                if not transient or attempt == MAX_RETRIES - 1:
                    raise
                last_error = exc
                match = RETRY_DELAY_RE.search(message)
                delay = int(match.group(1)) + 1 if match else 2 ** (attempt + 1)
                time.sleep(min(delay, 60))
        raise last_error or RuntimeError(  # pragma: no cover - loop returns or raises
            f"{self.name} exhausted {MAX_RETRIES} attempts without a response."
        )

    def _invoke(self, call: types.FunctionCall, trace: List[TraceEvent]) -> Dict[str, Any]:
        """Execute one tool call and record it on the trace."""
        args = dict(call.args or {})
        fn = self.tools.get(call.name)
        sub_agent = getattr(fn, "_agent", None) if fn else None

        # Record the delegation before running it, so the trace reads top-down:
        # the delegation line, then the sub-agent's own tool calls beneath it.
        event = TraceEvent(
            agent=self.name,
            kind="delegation" if sub_agent else "tool_call",
            name=call.name,
            args=args,
        )
        trace.append(event)

        if fn is None:
            result = {"status": "error", "message": f"Unknown tool '{call.name}'."}
        elif sub_agent is not None:
            # Share the parent's trace so nested tool calls stay visible.
            result = sub_agent.run_as_subagent(args.get("task", ""), trace=trace)
        else:
            try:
                result = fn(**args)
            except TypeError as exc:
                result = {"status": "error", "message": f"Invalid arguments for {call.name}: {exc}"}
            except Exception as exc:
                result = {"status": "error", "message": f"{call.name} failed: {type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            result = {"status": "success", "result": result}

        event.result = result
        return result

    def run(
        self,
        message: str,
        history: Optional[List[types.Content]] = None,
        trace: Optional[List[TraceEvent]] = None,
    ) -> str:
        """Run one turn to completion, executing tool calls until the model answers.

        Args:
            message: The user (or orchestrator) message for this turn.
            history: Mutable conversation history, carried across turns. The
                turn's messages are appended to it in place.
            trace: Mutable list collecting TraceEvents for observability.
        """
        contents = history if history is not None else []
        trace = trace if trace is not None else []
        contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
        config = self._config()

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._generate(contents, config)
            if not response.candidates:
                return "The model returned no response. Please try rephrasing your request."

            candidate = response.candidates[0]
            # Append verbatim so Gemini 3 thought signatures survive the round trip.
            if candidate.content is not None:
                contents.append(candidate.content)

            calls = response.function_calls
            if not calls:
                text = (response.text or "").strip()
                trace.append(TraceEvent(agent=self.name, kind="final", result=text))
                return text or "(no textual response)"

            response_parts = [
                types.Part.from_function_response(
                    name=call.name, response=self._invoke(call, trace)
                )
                for call in calls
            ]
            contents.append(types.Content(role="user", parts=response_parts))

        return (
            f"{self.name} stopped after {MAX_TOOL_ITERATIONS} tool steps without "
            "reaching a final answer. Please narrow the request."
        )

    def run_as_subagent(self, task: str, trace: List[TraceEvent]) -> Dict[str, Any]:
        """Run a delegated task with a fresh history but the caller's trace."""
        if not task or not task.strip():
            return {"status": "error", "message": f"No task was provided to the {self.name}."}
        marker = len(trace)
        try:
            answer = self.run(task, history=[], trace=trace)
        except Exception as exc:
            return {
                "status": "error",
                "agent": self.name,
                "message": f"{self.name} failed: {type(exc).__name__}: {exc}",
            }
        return {
            "status": "success",
            "agent": self.name,
            "response": answer,
            "tools_used": [e.name for e in trace[marker:] if e.kind == "tool_call"],
        }

    def as_tool(self) -> Callable:
        """Expose this agent to a parent agent as a single callable tool.

        The returned function carries a generated name, signature and docstring
        so the SDK can build a function declaration for it exactly as it does
        for an ordinary tool.
        """

        def delegate(task: str) -> dict:
            # Only ever called directly in tests; the orchestrator routes through
            # Agent._invoke so that the parent's trace is shared (see run_as_subagent).
            return self.run_as_subagent(task, trace=[])

        delegate.__name__ = f"delegate_to_{slugify(self.name)}"
        delegate.__doc__ = (
            f"Delegate a task to the {self.name}. {self.description}\n\n"
            "Args:\n"
            "    task: A complete, self-contained description of the task for this "
            "agent, including every detail it needs. The sub-agent cannot see the "
            "conversation, so restate names, emails, resume text and any other "
            "required values in full.\n\n"
            "Returns:\n"
            "    A dict with the sub-agent's response and the tools it used."
        )
        delegate._agent = self  # type: ignore[attr-defined]
        return delegate
