"""The copilot, running beside the room rather than inside it.

`adapters/agent/runner.run` is `async`; the bid loop is `sync` with an injected `time.sleep`.
Awaiting a query inside a cycle would stop the screen for as long as the model takes, which is
exactly what `docs/fantalab/00 §15` forbids — *il numero non dipende dalla rete*, and no pane
waits on a socket.

So the work happens on a **daemon** thread with its own event loop, and the render reads the
last answer non-blockingly. Daemon because SPEC §7 promises a second Ctrl-C exits: a
non-daemon thread mid-query would hold the process open past it.

**Advice is keyed by player, never by "whatever is on the block".** `counter_time` is 7-10 s
and a structured query takes seconds, so an answer about the current lot arrives describing a
lot that has already closed. The plan's top targets are briefed ahead, and when one comes up
his commentary is already there or it is absent — never late.

The runner is injected exactly as `news_fetcher` injects it, so the default test tier makes
zero agent calls. That claim is only provable because a raising worker thread now fails the
suite; before that gate, a live call on a thread was invisible.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable, Coroutine, Iterable, Sequence
from typing import Any

from fantabot.adapters.agent.options import AgentRequest
from fantabot.adapters.agent.runner import Outcome
from fantabot.adapters.agent.runner import run as sdk_run
from fantabot.domain.asta.copilot import SYSTEM_PROMPT, Commentary, CopilotBrief, brief_prompt

Runner = Callable[[AgentRequest, type[Commentary]], Coroutine[Any, Any, Outcome[Commentary]]]

#: Cheap and fast. The precompute pass is where a big model with search belongs; this one has
#: seconds, and the facts are already in the brief.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def request_for(brief: CopilotBrief, *, model: str = DEFAULT_MODEL) -> AgentRequest:
    """One player's question. Pure."""
    return AgentRequest(
        prompt=brief_prompt(brief),
        label=f"copilot:{brief.player_id}",
        model=model,
        allowed_tools=(),  # no search: the brief already carries every fact
        max_turns=1,
        system_prompt=SYSTEM_PROMPT,
    )


class CopilotWorker:
    """Briefs players in the background; the room reads whatever has arrived.

    `advice_for` never blocks and never raises. An outage, a timeout or a malformed answer all
    read the same way from the room's side — no commentary — which is the model-free baseline
    and the only failure mode the screen has to handle.
    """

    def __init__(
        self,
        *,
        runner: Runner = sdk_run,
        model: str = DEFAULT_MODEL,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._runner = runner
        self._model = model
        self._on_error = on_error
        self._queue: queue.Queue[CopilotBrief | None] = queue.Queue()
        self._answers: dict[str, Commentary] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.errors = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        # daemon: SPEC §7 promises a second Ctrl-C exits, and a non-daemon thread parked in a
        # query would hold the process open past it.
        self._thread = threading.Thread(target=self._pump, daemon=True, name="copilot")
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def brief(self, briefs: Iterable[CopilotBrief]) -> None:
        """Ask about these players, soonest first. Returns immediately."""
        for item in briefs:
            self._queue.put(item)

    def advice_for(self, player_id: str) -> Commentary | None:
        """Whatever has arrived for this player. Never blocks, never raises."""
        with self._lock:
            return self._answers.get(player_id)

    def _pump(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                self._ask(loop, item)
        finally:
            loop.close()

    def _ask(self, loop: asyncio.AbstractEventLoop, brief: CopilotBrief) -> None:
        # Every failure is the same failure from the room's side: no commentary. Letting one
        # escape would kill the thread and silently end the copilot for the evening — and with
        # the thread-exception gate in place it would now also fail the suite, which is the
        # point of catching it here rather than hoping.
        try:
            outcome = loop.run_until_complete(self._runner(request_for(brief, model=self._model), Commentary))
        except Exception as exc:
            self.errors += 1
            if self._on_error:
                self._on_error(f"{brief.name}: {exc}")
            return

        parsed = getattr(outcome, "value", None)
        if parsed is None:
            self.errors += 1
            return
        with self._lock:
            self._answers[brief.player_id] = parsed


def briefs_for(
    player_ids: Sequence[str],
    *,
    names: dict[str, str],
    teams: dict[str, str],
    roles: dict[str, Sequence[str]],
    walkaways: dict[str, float],
    prices: dict[str, float],
    credits_left: int,
    slots_left: int,
    schemi_open: int,
    recent: tuple[str, ...],
) -> list[CopilotBrief]:
    """Briefs for the plan's targets, richest walk-away first. Pure.

    Ordered because the queue is worked in order and the evening turns on four or five
    players: the ones we would pay most for are the ones worth a second reading.
    """
    ranked = sorted(player_ids, key=lambda pid: walkaways.get(pid, 0.0), reverse=True)
    return [
        CopilotBrief(
            player_id=pid,
            name=names.get(pid, pid),
            team=teams.get(pid, "?"),
            roles=tuple(roles.get(pid, ())),
            walk_away=int(walkaways.get(pid, 0)),
            observed_price=int(prices[pid]) if pid in prices else None,
            credits_left=credits_left,
            slots_left=slots_left,
            schemi_open=schemi_open,
            recent=recent,
        )
        for pid in ranked
    ]
