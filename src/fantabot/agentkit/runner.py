"""The one message loop.

Every fantabot command that queries an agent goes through ``consume``. The SDK
message loop is written exactly once, here, and a test in the suite fails if the
repo ever grows a second one.

An agent-level failure is **returned**, never raised: a bad subtype, a missing
structured output and a schema-rejected payload all come back as an
``Outcome`` carrying a reason, so one bad player cannot take down a 523-player
fan-out. Transport-level exceptions still propagate — those are the caller's
problem to retry.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, cast

from claude_agent_sdk import RateLimitEvent, query
from pydantic import BaseModel, ValidationError

from .env import assert_subscription_auth
from .options import AgentRequest, build_options

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class Outcome(Generic[M]):
    """The result of one agent query: a value, or a reason there isn't one."""

    value: M | None
    failure: str | None
    rate_limited: bool = False

    def __bool__(self) -> bool:
        return self.failure is None and self.value is not None


class ResultLike(Protocol):
    """The four fields ``classify_result`` reads, and nothing else.

    A Protocol rather than ``ResultMessage`` because that is genuinely all this
    function needs — which keeps the classification SDK-free and lets the tests
    hand it the real SDK type to prove the two shapes still agree.
    """

    @property
    def subtype(self) -> str: ...

    @property
    def errors(self) -> list[str] | None: ...

    @property
    def result(self) -> str | None: ...

    @property
    def structured_output(self) -> Any: ...


def classify_result(message: ResultLike, schema: type[M]) -> tuple[M | None, str | None]:
    """Turn one result message into either a validated model or a failure reason."""
    if message.subtype != "success":
        detail = "; ".join(message.errors) if message.errors else (message.result or "")
        reason = f"agent returned subtype {message.subtype!r}"
        return None, f"{reason}: {detail}" if detail else reason

    if message.structured_output is None:
        return None, "agent returned no structured output"

    try:
        return schema.model_validate(message.structured_output), None
    except ValidationError as exc:
        # Deliberately not clamped into range. An out-of-range score is a misread
        # prompt, and a clamp would hide it behind a plausible-looking row.
        return None, f"structured output failed the schema: {exc}"


async def consume(stream: AsyncIterator[Any], schema: type[M], label: str) -> Outcome[M]:
    """Drive a message stream to its result. Never raises on agent-level failure."""
    rate_limited = False

    # aclosing: the failure branch returns from the middle of this loop, leaving the
    # generator suspended. Left to the garbage collector its aclose() is deferred to
    # asyncio.run's shutdown_asyncgens and can fire while the generator's own
    # transport task is still live — "RuntimeError: aclose(): asynchronous generator
    # is already running". Closing it here runs its finally inside the live loop.
    async with aclosing(cast(AsyncGenerator[Any, None], stream)) as messages:
        async for message in messages:
            if isinstance(message, RateLimitEvent):
                info = message.rate_limit_info
                if info.status != "allowed":
                    rate_limited = True
                    log.warning(
                        "%s: rate limit status=%s utilization=%s resets_at=%s",
                        label,
                        info.status,
                        info.utilization,
                        info.resets_at,
                    )
                continue

            if hasattr(message, "subtype") and hasattr(message, "structured_output"):
                value, failure = classify_result(cast(ResultLike, message), schema)
                return Outcome(value=value, failure=failure, rate_limited=rate_limited)

    return Outcome(value=None, failure="stream ended without a result", rate_limited=rate_limited)


async def run(request: AgentRequest, schema: type[M]) -> Outcome[M]:
    """Build options, prove the auth, and consume the stream. The public entry point."""
    options = build_options(request, schema)
    assert_subscription_auth(options.env or {})
    return await consume(query(prompt=request.prompt, options=options), schema, request.label)
