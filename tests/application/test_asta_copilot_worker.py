"""The copilot runs beside the room, and the room never waits for it.

`runner.run` is async and the bid loop is sync, so a query awaited inside a cycle would stop
the screen for as long as the model takes — the one thing `docs/fantalab/00 §15` forbids. The
work goes on a daemon thread and the render reads whatever has arrived.

Every test here injects the runner, so the default tier makes zero agent calls. That is only
*provable* because a raising worker thread now fails the suite: before that gate a live call
on a thread was invisible, and A13b would have been met by a suite that permitted one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fantabot.application.asta_copilot import CopilotWorker, briefs_for, request_for
from fantabot.domain.asta.copilot import Commentary, CopilotBrief

BRIEF = CopilotBrief(
    player_id="200", name="Bomber", team="MIL", roles=("A",), walk_away=77,
    observed_price=80, credits_left=309, slots_left=22, schemi_open=7, recent=(),
)
SAID = Commentary(
    headline="ballottaggio aperto", why="due punte per un posto",
    confidence="medium", disagrees_with_plan=True,
)


@dataclass
class _Outcome:
    value: Commentary | None


def _worker(answer=SAID, boom=False, slow=0.0, **kw):  # type: ignore[no-untyped-def]
    async def runner(_request, _schema):  # type: ignore[no-untyped-def]
        if slow:
            await asyncio.sleep(slow)
        if boom:
            raise RuntimeError("the model is down")
        return _Outcome(value=answer)

    return CopilotWorker(runner=runner, **kw)


def _settle(worker: CopilotWorker, player_id: str, timeout: float = 2.0) -> Commentary | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = worker.advice_for(player_id)
        if found is not None:
            return found
        time.sleep(0.01)
    return None


class TestItAnswersOutOfBand:
    def test_a_briefed_player_gets_commentary(self) -> None:
        worker = _worker()
        worker.start()
        worker.brief([BRIEF])
        try:
            assert _settle(worker, "200") == SAID
        finally:
            worker.stop()

    def test_asking_before_the_answer_lands_returns_none_rather_than_blocking(self) -> None:
        """The render calls this every frame. If it could block, the screen could stop."""
        worker = _worker(slow=5.0)
        worker.start()
        worker.brief([BRIEF])
        try:
            started = time.monotonic()
            assert worker.advice_for("200") is None
            assert time.monotonic() - started < 0.1
        finally:
            worker.stop()

    def test_an_unbriefed_player_is_simply_absent(self) -> None:
        assert _worker().advice_for("nobody") is None


class TestEveryFailureLooksTheSame:
    def test_a_runner_that_raises_does_not_kill_the_thread(self) -> None:
        """An escaping exception would end the copilot for the evening — silently before the
        thread-exception gate, and as a red suite after it."""
        worker = _worker(boom=True)
        worker.start()
        worker.brief([BRIEF])
        try:
            _settle(worker, "200", timeout=0.5)
            assert worker.advice_for("200") is None
            assert worker.errors == 1
            worker.brief([BRIEF])  # still alive
            _settle(worker, "200", timeout=0.5)
            assert worker.errors == 2
        finally:
            worker.stop()

    def test_an_unparseable_answer_is_no_answer(self) -> None:
        worker = _worker(answer=None)
        worker.start()
        worker.brief([BRIEF])
        try:
            _settle(worker, "200", timeout=0.5)
            assert worker.advice_for("200") is None
        finally:
            worker.stop()


class TestTheRequest:
    def test_it_carries_no_tools_because_the_brief_carries_the_facts(self) -> None:
        """Search is the precompute pass's job. Here it is latency for nothing."""
        assert request_for(BRIEF).allowed_tools == ()

    def test_one_turn_only(self) -> None:
        assert request_for(BRIEF).max_turns == 1

    def test_the_system_prompt_forbids_a_price_in_words_too(self) -> None:
        """The schema is the enforcement; the prompt saves a round trip arguing about it."""
        assert "cifra" in request_for(BRIEF).system_prompt


class TestBriefOrder:
    def test_the_dearest_targets_are_briefed_first(self) -> None:
        """The queue is worked in order and the evening turns on four or five players."""
        briefs = briefs_for(
            ["a", "b", "c"],
            names={}, teams={}, roles={}, prices={"b": 50.0},
            walkaways={"a": 10.0, "b": 90.0, "c": 40.0},
            credits_left=100, slots_left=5, schemi_open=3, recent=(),
        )

        assert [b.player_id for b in briefs] == ["b", "c", "a"]

    def test_a_player_nobody_has_bought_carries_no_observed_price(self) -> None:
        briefs = briefs_for(
            ["a"], names={}, teams={}, roles={}, prices={}, walkaways={"a": 10.0},
            credits_left=100, slots_left=5, schemi_open=3, recent=(),
        )

        assert briefs[0].observed_price is None
