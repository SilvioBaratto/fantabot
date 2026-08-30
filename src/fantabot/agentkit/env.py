"""Environment hygiene: force the Claude Code subscription, never an API key.

The Agent SDK spawns the Claude Code CLI as a subprocess. With no Anthropic
credential reachable, that CLI falls back to the OAuth profile in ``~/.claude`` —
which is what we want, because the subscription does not bill per token and this
pipeline issues 523 queries a week.

There are **two** leak vectors, and closing only the obvious one closes nothing:

1. ``os.environ`` — cleared by :func:`strip_dangerous_env`.
2. ``ClaudeAgentOptions.env`` — ``claude_agent_sdk/_internal/session_resume.py:356``
   reads ``opt_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")``,
   so a spotless ``os.environ`` proves nothing on its own.

:func:`assert_subscription_auth` checks both and is the only auth proof there is.
``ResultMessage.total_cost_usd`` is not evidence: on the subscription a populated
value is a CLI estimate, not a charge.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Every credential or backend toggle a run must not inherit unasked from the ambient
# shell. Two kinds of entry live here and both matter:
#   - secrets (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, the Bedrock/Vertex keys), and
#   - routing switches (CLAUDE_CODE_USE_*), which carry no secret at all but silently
#     send the run to a per-token backend. A CLAUDE_CODE_USE_BEDROCK exported in the
#     operator's shell must not quietly bill an AWS account for a Wednesday cron.
DANGEROUS_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BEDROCK_API_KEY",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
)


class AuthLeakError(RuntimeError):
    """A credential or backend toggle reached a run that must use the subscription."""


def strip_dangerous_env() -> None:
    """Clear every :data:`DANGEROUS_VARS` entry from ``os.environ``.

    Idempotent, and deliberately narrow: an unrelated variable the rest of fantabot
    depends on (``LEGA_EMAIL`` and friends) is none of this function's business.
    """
    for name in DANGEROUS_VARS:
        os.environ.pop(name, None)


def assert_subscription_auth(options_env: Mapping[str, str]) -> None:
    """Raise unless both credential channels are clean.

    ``options_env`` is whatever is about to be handed to ``ClaudeAgentOptions.env``.
    An **empty string** is not a leak: that is how a caller explicitly neutralizes a
    variable, and treating it as a credential would reject the safe case.
    """
    for name in DANGEROUS_VARS:
        if options_env.get(name):
            raise AuthLeakError(
                f"{name} is set in options.env; this run must use the Claude Code "
                f"OAuth subscription. See session_resume.py:356 — the SDK reads the "
                f"key from options.env as well as os.environ."
            )
        if os.environ.get(name):
            raise AuthLeakError(
                f"{name} is set in os.environ; this run must use the Claude Code "
                f"OAuth subscription. Call strip_dangerous_env() before building "
                f"options."
            )


def assert_byo_backend(options_env: Mapping[str, str]) -> None:
    """Raise unless the run is genuinely pointed at a shim.

    The mirror image of :func:`assert_subscription_auth`. There the proof is "no
    credential anywhere". Here it is "a base URL and a token" — because
    ``ANTHROPIC_BASE_URL`` with no token makes the CLI fall back to the OAuth
    subscription and silently ignore the shim, which looks identical to working
    until the bill or the rate limit says otherwise.
    """
    if not options_env.get("ANTHROPIC_BASE_URL"):
        raise AuthLeakError(
            "ANTHROPIC_BASE_URL is missing from options.env; without it the CLI "
            "falls back to the OAuth subscription and the shim is ignored."
        )
    if not options_env.get("ANTHROPIC_AUTH_TOKEN"):
        raise AuthLeakError(
            "ANTHROPIC_AUTH_TOKEN is missing from options.env; the CLI will not "
            "start against a custom base URL without one. Ollama ignores the "
            "value — 'ollama' is the documented placeholder."
        )
    for name in DANGEROUS_VARS:
        if os.environ.get(name):
            raise AuthLeakError(
                f"{name} is set in os.environ and would race options.env. "
                f"Call strip_dangerous_env() before building options."
            )


def assert_auth(options_env: Mapping[str, str]) -> None:
    """Dispatch to whichever proof this run's backend needs. The only entry point.

    Imported inside the function, not at module scope: ``config`` instantiates
    ``Settings`` at import, and this module sits on the import chain of every
    agent command.
    """
    from fantabot.config import settings

    if settings.fantabot_agent_base_url:
        assert_byo_backend(options_env)
    else:
        assert_subscription_auth(options_env)
