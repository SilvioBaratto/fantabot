"""The participant bid loop: read the live lot, decide, write (gated), speak while it runs.

A run with no end — the exit summary is never reached — so it emits a heartbeat every cycle and
counts refusals per guard, the numbers you need to tell a lost race from a bid never sent
(``docs/fantalab/06-asta-write-path.md`` §9). Every effect is **injected** — the snapshot read,
the write, the clock, the sleep, the heartbeat sink, and the target picker — so the whole loop is
tested with fakes: no socket, no PATCH. The decision itself is the pure ``asta_engine.bid``.

Participant only: the loop bids, it never settles. ``close_auction``/``confirm`` are the admin's,
so a human (or an admin bot) closes each lot; this loop just chases its targets to their
walk-away and holds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fantabot.asta_engine.bid import Seat, decide_bid, pass_reason

Snapshot = Mapping[str, Any]


@dataclass(frozen=True)
class LoopReport:
    """What a loop run did — the summary a never-ending run would otherwise never reach."""

    cycles: int
    bids_sent: int
    refused: dict[str, int]


def run_bid_loop(
    *,
    seat: Seat,
    fantaleague_id: str,
    remaining_budget: int,
    target_of: Callable[[Snapshot], tuple[str, int] | None],
    read: Callable[[], Snapshot | None],
    write: Callable[[dict[str, Any]], Any],
    now: Callable[[], int],
    sleep: Callable[[float], None],
    keep_going: Callable[[int], bool],
    heartbeat: Callable[[str], None],
    poll_seconds: float = 2.0,
    step: int = 1,
) -> LoopReport:
    """Poll the room, bid our target up to its walk-away, and report.

    ``target_of`` maps the live snapshot to ``(target_player_id, walk_away)`` — the advisory's
    current pick and its reservation price — or ``None`` when the lot on the block is not one we
    chase. ``write`` is a bound ``rtdb.place_raise`` (gated by ``FANTABOT_AUTO_ACT``): its
    ``.sent`` says whether a PATCH actually went out. ``keep_going(cycle)`` bounds the run.
    """
    cycles = 0
    bids_sent = 0
    refused: dict[str, int] = {}

    while keep_going(cycles):
        cycles += 1
        snapshot = read()
        if not snapshot or not isinstance(snapshot.get("player_id"), str):
            heartbeat(f"[{cycles}] waiting for a lot")
            sleep(poll_seconds)
            continue

        pick = target_of(snapshot)
        if pick is None:
            heartbeat(f"[{cycles}] on the block {snapshot.get('player_id')} — not a target, hold")
            sleep(poll_seconds)
            continue

        target, walk_away = pick
        now_ms = now()
        payload = decide_bid(
            snapshot,
            seat,
            fantaleague_id,
            target=target,
            walk_away=walk_away,
            remaining_budget=remaining_budget,
            now_ms=now_ms,
            step=step,
        )
        if payload is None:
            reason = (
                pass_reason(
                    snapshot,
                    seat,
                    target=target,
                    walk_away=walk_away,
                    remaining_budget=remaining_budget,
                    now_ms=now_ms,
                    step=step,
                )
                or "none"
            )
            refused[reason] = refused.get(reason, 0) + 1
            heartbeat(f"[{cycles}] pass on {target}: {reason}")
            sleep(poll_seconds)
            continue

        outcome = write(payload)
        sent = bool(getattr(outcome, "sent", False))
        bids_sent += int(sent)
        verb = "BID" if sent else "dry-run"
        heartbeat(f"[{cycles}] {verb} {payload['price']} on {target} (status {getattr(outcome, 'status', None)})")
        sleep(poll_seconds)

    return LoopReport(cycles=cycles, bids_sent=bids_sent, refused=refused)


__all__ = ["LoopReport", "Snapshot", "run_bid_loop"]
