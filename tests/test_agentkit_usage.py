"""Token-usage extraction from an SDK result message, and its accumulation.

Pure and synchronous — no asyncio, so it runs on any platform. The async wiring
that calls ``extract_usage`` inside ``consume`` is covered by
``test_agentkit_runner.py``; here we pin the extraction and the arithmetic alone.

The field shapes mirror the real SDK (measured 2026-08-28): ``ResultMessage.usage``
is a snake_case dict, ``ResultMessage.model_usage`` maps a model id to a
``ModelUsage`` TypedDict with camelCase keys (``inputTokens``,
``cacheReadInputTokens``, ``costUSD``, ...), and ``total_cost_usd`` is a float or
None.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fantabot.agentkit.runner import Usage, extract_usage


def _msg(**kwargs: Any) -> SimpleNamespace:
    kwargs.setdefault("model_usage", None)
    kwargs.setdefault("usage", None)
    kwargs.setdefault("total_cost_usd", None)
    return SimpleNamespace(**kwargs)


def test_usage_adds_field_by_field() -> None:
    a = Usage(input_tokens=1, output_tokens=2, cache_read_tokens=3, cache_creation_tokens=4, cost_usd=0.5)
    b = Usage(input_tokens=10, output_tokens=20, cache_read_tokens=30, cache_creation_tokens=40, cost_usd=1.5)
    total = a + b
    assert (total.input_tokens, total.output_tokens) == (11, 22)
    assert (total.cache_read_tokens, total.cache_creation_tokens) == (33, 44)
    assert total.cost_usd == 2.0


def test_adding_none_cost_keeps_the_other_side() -> None:
    # None is "no estimate", not zero: it must not swallow a real number.
    assert (Usage(cost_usd=None) + Usage(cost_usd=0.3)).cost_usd == 0.3
    assert (Usage(cost_usd=0.3) + Usage(cost_usd=None)).cost_usd == 0.3
    assert (Usage() + Usage()).cost_usd is None


def test_extract_prefers_model_usage_and_sums_across_models() -> None:
    msg = _msg(
        model_usage={
            "claude-sonnet-4-6-eaq-gf08h1": {
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadInputTokens": 900,
                "cacheCreationInputTokens": 200,
                "costUSD": 0.01,
            },
            "claude-haiku": {
                "inputTokens": 10,
                "outputTokens": 5,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "costUSD": 0.001,
            },
        },
        total_cost_usd=0.011,
    )
    u = extract_usage(msg)
    assert u.input_tokens == 110
    assert u.output_tokens == 55
    assert u.cache_read_tokens == 900
    assert u.cache_creation_tokens == 200
    # total_cost_usd is authoritative whole-tree cost, preferred over per-model sum.
    assert u.cost_usd == 0.011


def test_extract_sums_per_model_cost_when_total_absent() -> None:
    msg = _msg(model_usage={"a": {"costUSD": 0.02}, "b": {"costUSD": 0.03}}, total_cost_usd=None)
    assert extract_usage(msg).cost_usd == 0.05


def test_extract_falls_back_to_usage_when_no_model_usage() -> None:
    msg = _msg(
        usage={
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_input_tokens": 70,
            "cache_creation_input_tokens": 5,
        },
    )
    u = extract_usage(msg)
    assert (u.input_tokens, u.output_tokens) == (7, 3)
    assert (u.cache_read_tokens, u.cache_creation_tokens) == (70, 5)
    assert u.cost_usd is None


def test_extract_tolerates_missing_fields() -> None:
    # An empty message must read as zero, never raise: a run that cannot price
    # itself must still store its readings.
    assert extract_usage(_msg()) == Usage()
    partial = extract_usage(_msg(model_usage={"m": {"inputTokens": 4}}))
    assert partial.input_tokens == 4
    assert partial.output_tokens == 0
    assert partial.cache_read_tokens == 0
    assert partial.cost_usd is None
