"""Options translation, and the system-prompt caching hook. Pure and synchronous.

``build_options`` turns an ``AgentRequest`` into ``ClaudeAgentOptions``. Task 2 adds
one thing: a non-empty ``system_prompt`` on the request is sent as a Claude Code
preset with an ``append``, so the stable instructions extend the cached system block
instead of riding in every per-player user message. An empty one leaves the default
system prompt untouched.
"""

from __future__ import annotations

from pydantic import BaseModel

from fantabot.agentkit.options import AgentRequest, build_options


class _Schema(BaseModel):
    x: int = 0


def _request(system_prompt: str = "") -> AgentRequest:
    return AgentRequest(
        prompt="analizza",
        label="Tizio",
        model="claude-sonnet-4-6-eaq-gf08h1",
        allowed_tools=("WebSearch", "WebFetch"),
        max_turns=6,
        system_prompt=system_prompt,
    )


def test_agent_request_defaults_system_prompt_to_empty() -> None:
    # mantra_grid builds an AgentRequest without a system prompt; the field must
    # default so that caller keeps working.
    req = AgentRequest(
        prompt="p", label="l", model="claude-x", allowed_tools=("WebSearch",), max_turns=3
    )
    assert req.system_prompt == ""


def test_non_empty_system_prompt_becomes_a_preset_append() -> None:
    options = build_options(_request("ISTRUZIONI STABILI"), _Schema)
    assert options.system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": "ISTRUZIONI STABILI",
    }


def test_empty_system_prompt_leaves_the_default() -> None:
    # None means "use Claude Code's own system prompt" — not an empty custom one.
    options = build_options(_request(""), _Schema)
    assert options.system_prompt is None
