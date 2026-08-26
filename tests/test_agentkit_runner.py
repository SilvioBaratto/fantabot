"""T2: the options builder and the one message loop.

Tests drive real ``ResultMessage`` / ``RateLimitEvent`` instances rather than
hand-rolled doubles. The SDK types structurally satisfy the ``ResultLike``
Protocol, and using them here is what proves that — a fake satisfying a Protocol
only ever proves the fake does.

No test spawns an SDK subprocess: ``consume()`` takes any async iterable, and
the async cases are driven through ``asyncio.run`` rather than a plugin — this
suite adds no third-party test dependency.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import RateLimitEvent, ResultMessage
from claude_agent_sdk.types import RateLimitInfo
from pydantic import BaseModel, ConfigDict, Field

from fantabot.agentkit.options import AgentRequest, build_options
from fantabot.agentkit.runner import Outcome, classify_result, consume


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    note: str


def _request(**overrides: Any) -> AgentRequest:
    defaults: dict[str, Any] = {
        "prompt": "who is this player",
        "label": "test",
        "model": "claude-sonnet-5",
        "allowed_tools": ("WebSearch", "WebFetch"),
        "max_turns": 12,
    }
    return AgentRequest(**{**defaults, **overrides})


def _result(**overrides: Any) -> ResultMessage:
    defaults: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 10,
        "duration_api_ms": 8,
        "is_error": False,
        "num_turns": 2,
        "session_id": "s-1",
        "structured_output": {"score": 0.5, "note": "ok"},
    }
    return ResultMessage(**{**defaults, **overrides})


def _rate_limit(status: str) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(
            status=status,  # type: ignore[arg-type]
            resets_at=None,
            rate_limit_type="five_hour",
            utilization=0.9,
            overage_status=None,
            overage_resets_at=None,
            overage_disabled_reason=None,
            raw={},
        ),
        uuid="u-1",
        session_id="s-1",
    )


async def _stream(*messages: Any) -> AsyncIterator[Any]:
    for message in messages:
        yield message


# --- build_options -------------------------------------------------------


def test_build_options_carries_model_and_tools() -> None:
    options = build_options(_request(), Sample)

    assert options.model == "claude-sonnet-5"
    assert options.allowed_tools == ["WebSearch", "WebFetch"]
    assert options.max_turns == 12


def test_build_options_leaves_env_empty() -> None:
    # The second leak vector. See agentkit/env.py.
    assert build_options(_request(), Sample).env == {}


def test_build_options_blocks_delegation() -> None:
    # An agent that spawns subagents returns prose instead of structured output.
    disallowed = build_options(_request(), Sample).disallowed_tools
    assert "Task" in disallowed
    assert "Agent" in disallowed


def test_build_options_blocks_file_and_shell_tools() -> None:
    disallowed = build_options(_request(), Sample).disallowed_tools
    assert {"Bash", "Write", "Edit"} <= set(disallowed)


def test_build_options_loads_no_setting_sources() -> None:
    # Without this, every one of 523 queries loads this repo's CLAUDE.md, its
    # hooks and its skills. The agent needs to know about one footballer.
    assert build_options(_request(), Sample).setting_sources == []


def test_build_options_requests_the_schema_as_structured_output() -> None:
    output_format = build_options(_request(), Sample).output_format

    assert output_format is not None
    assert output_format["type"] == "json_schema"
    assert output_format["schema"] == Sample.model_json_schema()


def test_build_options_raises_the_buffer_above_the_sdk_default() -> None:
    # The SDK defaults to a 1 MiB message buffer. A WebFetch of a large page
    # overflows it and the whole query dies with CLIJSONDecodeError — observed
    # live on the first mantra-grid --write run. WebFetch is the entire point of
    # both commands, so the default is not survivable.
    assert build_options(_request(), Sample).max_buffer_size is not None
    assert build_options(_request(), Sample).max_buffer_size > 1024 * 1024


def test_build_options_bypasses_permission_prompts() -> None:
    # Unattended cron; every granted tool is read-only.
    assert build_options(_request(), Sample).permission_mode == "bypassPermissions"


# --- classify_result -----------------------------------------------------


def test_classify_result_returns_the_validated_model() -> None:
    value, failure = classify_result(_result(), Sample)

    assert failure is None
    assert value == Sample(score=0.5, note="ok")


def test_classify_result_rejects_a_non_success_subtype() -> None:
    value, failure = classify_result(_result(subtype="error_max_turns"), Sample)

    assert value is None
    assert failure is not None
    assert "error_max_turns" in failure


def test_classify_result_rejects_missing_structured_output() -> None:
    value, failure = classify_result(_result(structured_output=None), Sample)

    assert value is None
    assert failure is not None
    assert "structured output" in failure.lower()


def test_classify_result_rejects_output_failing_the_schema() -> None:
    # score=1.4 is out of range. A clamp here would hide a misread prompt.
    value, failure = classify_result(
        _result(structured_output={"score": 1.4, "note": "ok"}), Sample
    )

    assert value is None
    assert failure is not None
    assert "score" in failure


def test_classify_result_surfaces_sdk_errors_in_the_failure() -> None:
    value, failure = classify_result(
        _result(subtype="error_during_execution", errors=["upstream exploded"]), Sample
    )

    assert value is None
    assert failure is not None
    assert "upstream exploded" in failure


# --- consume -------------------------------------------------------------


def test_consume_returns_the_value_from_the_result_message() -> None:
    outcome = asyncio.run(consume(_stream(_result()), Sample, label="t"))

    assert outcome.value == Sample(score=0.5, note="ok")
    assert outcome.failure is None


def test_consume_returns_a_failure_instead_of_raising() -> None:
    # One bad player must not take down a 523-player fan-out.
    outcome = asyncio.run(consume(_stream(_result(subtype="error_max_turns")), Sample, label="t"))

    assert outcome.value is None
    assert outcome.failure is not None


def test_consume_tolerates_a_rejected_rate_limit_event() -> None:
    outcome = asyncio.run(consume(_stream(_rate_limit("rejected"), _result()), Sample, label="t"))

    assert outcome.failure is None
    assert outcome.value == Sample(score=0.5, note="ok")
    assert outcome.rate_limited is True


def test_consume_does_not_flag_an_allowed_rate_limit_event() -> None:
    outcome = asyncio.run(consume(_stream(_rate_limit("allowed"), _result()), Sample, label="t"))

    assert outcome.rate_limited is False


def test_consume_reports_a_stream_that_never_yields_a_result() -> None:
    outcome = asyncio.run(consume(_stream(), Sample, label="t"))

    assert outcome.value is None
    assert outcome.failure is not None


def test_consume_closes_the_stream_on_the_early_return_path() -> None:
    # Returning out of the middle of the loop leaves the generator suspended.
    # Left to the GC its aclose() can fire while its transport task is still
    # live — "aclose(): asynchronous generator is already running".
    closed = False

    async def tracked() -> AsyncIterator[Any]:
        nonlocal closed
        try:
            yield _result(subtype="error_max_turns")
            yield _result()
        finally:
            closed = True

    outcome = asyncio.run(consume(tracked(), Sample, label="t"))

    assert outcome.failure is not None
    assert closed is True


def test_outcome_is_falsy_when_it_carries_a_failure() -> None:
    assert not Outcome[Sample](value=None, failure="boom")
    assert Outcome[Sample](value=Sample(score=0.1, note="x"), failure=None)


def test_the_repo_has_exactly_one_message_loop() -> None:
    """docs/spec-news-sentiment.md success criterion 14, enforced rather than asserted in prose.

    The sibling optimizer-theory repo had to undo five copies of this loop. The
    cheapest moment to stop the second one is before it is written.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "fantabot"
    loops = [
        f"{path.relative_to(src)}:{n}"
        for path in sorted(src.rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if line.lstrip().startswith("async for message")
    ]

    assert len(loops) == 1, loops
    assert loops[0].startswith("agentkit/runner.py:"), loops


def test_only_agentkit_imports_the_sdk() -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "fantabot"
    offenders = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path.parent.name != "agentkit" and "claude_agent_sdk" in path.read_text()
    )

    assert offenders == [], offenders
