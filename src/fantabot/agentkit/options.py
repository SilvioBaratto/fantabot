"""Translate a fantabot request into ``ClaudeAgentOptions``.

Four of these settings are load-bearing and none of them is obvious, so each
carries its reason at the point of decision rather than in a doc nobody reads
next to the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions
from pydantic import BaseModel

# Delegation, shell and filesystem access, blocked by name. Two different hazards:
#   - Task/Agent: an agent that spawns subagents answers with prose instead of the
#     structured output this pipeline validates, and burns the concurrency budget
#     doing it.
#   - Bash/Write/Edit/NotebookEdit: a query that reads football news has no reason
#     to touch this machine. Granting nothing is cheaper than auditing later.
# The SDK defaults to a 1 MiB message buffer. A WebFetch of a large page overflows
# it and the query dies with CLIJSONDecodeError, killing the whole run — observed
# live, mid-collection, on a rules page. Both commands exist to fetch web pages, so
# the default is not survivable. 16 MiB is comfortably above any single article or
# rules table and still bounded.
MAX_BUFFER_BYTES = 16 * 1024 * 1024

BLOCKED_TOOLS: tuple[str, ...] = (
    "Task",
    "Agent",
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
)


@dataclass(frozen=True)
class AgentRequest:
    """One unit of agent work. Frozen, like the rest of fantabot's value types."""

    prompt: str
    label: str
    model: str
    allowed_tools: tuple[str, ...]
    max_turns: int


def agent_env() -> dict[str, str]:
    """The subprocess environment handed to the Claude Code CLI.

    Empty on the subscription path — that emptiness is load-bearing, see env.py.
    Populated only when an operator has explicitly pointed fantabot at an
    Anthropic-compatible shim (Ollama, LiteLLM, a gateway). options.env is the
    only channel used either way, so a stripped os.environ still cannot leak.
    """
    from ..config import settings

    if not settings.fantabot_agent_base_url:
        return {}
    return {
        "ANTHROPIC_BASE_URL": settings.fantabot_agent_base_url,
        "ANTHROPIC_AUTH_TOKEN": settings.fantabot_agent_auth_token,
        # Empty string, not absent — that is how a caller neutralizes a variable
        # without it counting as a credential (see assert_subscription_auth).
        "ANTHROPIC_API_KEY": "",
    }


def build_options(request: AgentRequest, schema: type[BaseModel]) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=request.model,
        # Empty on the subscription path, and checked by assert_auth before any
        # query: the SDK reads ANTHROPIC_API_KEY from options.env as well as
        # os.environ (session_resume.py:356), so leaving this unset is half the
        # auth proof. Non-empty only for an explicitly configured shim.
        env=agent_env(),
        allowed_tools=list(request.allowed_tools),
        disallowed_tools=list(BLOCKED_TOOLS),
        # [] and not None. Without it the SDK loads this repo's CLAUDE.md, its hooks
        # and its skills into every single query — 523 of them a week. The agent
        # needs to know about one footballer, not about fantabot.
        setting_sources=[],
        # The schema IS the contract. Asking for prose and parsing it afterwards is
        # what this pipeline exists to stop doing.
        output_format={"type": "json_schema", "schema": schema.model_json_schema()},
        # A backstop, not a target: roughly six searches plus fetches. Without it one
        # pathological player can spend the whole run's budget.
        max_turns=request.max_turns,
        max_buffer_size=MAX_BUFFER_BYTES,
        # Unattended cron, and every granted tool is read-only.
        # cast: the SDK types this as a Literal union; the value is a constant here.
        permission_mode=cast("Any", "bypassPermissions"),
    )
