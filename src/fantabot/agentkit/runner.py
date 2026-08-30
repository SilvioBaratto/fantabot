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
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, cast

from claude_agent_sdk import RateLimitEvent, query
from pydantic import BaseModel, ValidationError

from fantabot.agentkit.env import assert_auth
from fantabot.agentkit.options import AgentRequest, build_options

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def _cost_add(a: float | None, b: float | None) -> float | None:
    """Sum two optional dollar estimates. None is 'no estimate', not zero, so a
    real number on either side survives and two Nones stay None."""
    if a is None:
        return b
    if b is None:
        return a
    return a + b


@dataclass(frozen=True)
class Usage:
    """Token counts (and an optional dollar estimate) for one or many queries.

    The dollar figure is the SDK's client-side estimate and can be None or 0 for a
    model its bundled price table does not recognise — likely for a custom Foundry
    id — so the load-bearing fields are the token counts. Adds field-by-field so a
    run can fold every query's usage into one figure.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_creation_tokens + other.cache_creation_tokens,
            _cost_add(self.cost_usd, other.cost_usd),
        )


def extract_usage(message: Any) -> Usage:
    """Read token usage off an SDK result message. Never raises on a missing field.

    Prefers ``model_usage`` — the whole-tree, per-model breakdown (camelCase keys on
    a ``ModelUsage`` TypedDict) — because ``usage`` alone undercounts once subagents
    run. Cost comes from ``total_cost_usd`` (authoritative) when present, else the
    sum of per-model ``costUSD``. Falls back to the snake_case ``usage`` dict when no
    ``model_usage`` is reported.
    """
    total_cost = getattr(message, "total_cost_usd", None)
    model_usage = getattr(message, "model_usage", None)
    if model_usage:
        acc = Usage()
        per_model_cost: float | None = None
        for per in model_usage.values():
            acc = acc + Usage(
                input_tokens=int(per.get("inputTokens", 0) or 0),
                output_tokens=int(per.get("outputTokens", 0) or 0),
                cache_read_tokens=int(per.get("cacheReadInputTokens", 0) or 0),
                cache_creation_tokens=int(per.get("cacheCreationInputTokens", 0) or 0),
            )
            cost = per.get("costUSD")
            if cost is not None:
                per_model_cost = (per_model_cost or 0.0) + float(cost)
        return Usage(
            acc.input_tokens,
            acc.output_tokens,
            acc.cache_read_tokens,
            acc.cache_creation_tokens,
            total_cost if total_cost is not None else per_model_cost,
        )
    usage = getattr(message, "usage", None) or {}
    return Usage(
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        cost_usd=total_cost,
    )


@dataclass(frozen=True)
class Outcome(Generic[M]):
    """The result of one agent query: a value, or a reason there isn't one."""

    value: M | None
    failure: str | None
    rate_limited: bool = False
    usage: Usage = field(default_factory=Usage)

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
                return Outcome(
                    value=value,
                    failure=failure,
                    rate_limited=rate_limited,
                    usage=extract_usage(message),
                )

    return Outcome(value=None, failure="stream ended without a result", rate_limited=rate_limited)


async def run(request: AgentRequest, schema: type[M]) -> Outcome[M]:
    """Build options, prove the auth, and consume the stream. The public entry point."""
    options = build_options(request, schema)
    assert_auth(options.env or {})
    return await consume(query(prompt=request.prompt, options=options), schema, request.label)
