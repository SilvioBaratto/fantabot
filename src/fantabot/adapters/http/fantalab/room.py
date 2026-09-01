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
from dataclasses import dataclass, field
from typing import Any

from fantabot.domain.asta.bid import Seat, decide_bid, pass_reason

Snapshot = Mapping[str, Any]


@dataclass(frozen=True)
class LoopReport:
    """What a loop run did — the summary a never-ending run would otherwise never reach."""

    cycles: int
    bids_sent: int
    refused: dict[str, int]
    #: Failed cycles by exception class name. Not decoration: a run reporting 400 cycles and
    #: 0 bids reads as "nothing we wanted came up" until this says `{'ReadTimeout': 400}`.
    errors: dict[str, int] = field(default_factory=dict)


def run_bid_loop(
    *,
    seat: Seat,
    fantaleague_id: str,
    remaining_budget: int | Callable[[], int],
    max_cap: int | Callable[[], int] | None,
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

    ``max_cap`` is **required, not defaulted**. `decide_bid` is called from inside this loop,
    so the interface has no seam of its own to add the cap at; a default here would let a call
    site omit the one guard standing between a "pay anything" walk-away and an unfieldable
    rosa, and omit it silently. Pass ``None`` to mean "no cap" — deliberately, in writing.

    ``remaining_budget`` may be a callable, and in a live room it must be. It was a plain int
    passed once, so after the first lot won the budget guard — the one thing between a plan and
    an overdraft — compared every bid against a number that had stopped being true. The caller
    reads the ledger each cycle anyway; this lets the loop see what it found.

    **``KeyboardInterrupt`` returns the report rather than propagating.** In production
    ``keep_going`` is ``lambda _cycle: True``, so this loop never ends on its own and Ctrl-C is
    how every real run finishes. Letting it escape threw away the only summary of the evening —
    cycles, bids sent, and which guard refused the rest — all of which was computed and then
    lost at the exact moment the operator wanted it.
    """
    cycles = 0
    bids_sent = 0
    refused: dict[str, int] = {}
    errors: dict[str, int] = {}

    def budget_now() -> int:
        return remaining_budget() if callable(remaining_budget) else remaining_budget

    def cap_now() -> int | None:
        return max_cap() if callable(max_cap) else max_cap

    # `break` inside this frame rather than a `try` wrapped around the whole call: the counters
    # have to stay here, or the report the interrupt exists to preserve is lost with them.
    while keep_going(cycles):
        try:
            cycles += 1
            snapshot = read()
            if not snapshot or not isinstance(snapshot.get("player_id"), str):
                heartbeat(f"[{cycles}] waiting for a lot")
                sleep(poll_seconds)
                continue

            pick = target_of(snapshot)
            if pick is None:
                heartbeat(
                    f"[{cycles}] on the block {snapshot.get('player_id')} — not a target, hold"
                )
                sleep(poll_seconds)
                continue

            target, walk_away = pick
            now_ms = now()
            budget = budget_now()
            cap = cap_now()
            payload = decide_bid(
                snapshot,
                seat,
                fantaleague_id,
                target=target,
                walk_away=walk_away,
                remaining_budget=budget,
                now_ms=now_ms,
                step=step,
                max_cap=cap,
            )
            if payload is None:
                reason = (
                    pass_reason(
                        snapshot,
                        seat,
                        target=target,
                        walk_away=walk_away,
                        remaining_budget=budget,
                        now_ms=now_ms,
                        step=step,
                        max_cap=cap,
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
            heartbeat(
                f"[{cycles}] {verb} {payload['price']} on {target} "
                f"(status {getattr(outcome, 'status', None)})"
            )
            sleep(poll_seconds)
        except KeyboardInterrupt:
            # The operator, not a fault. Never swallowed.
            heartbeat(f"[{cycles}] interrupted")
            break
        except Exception as exc:
            # **One bad poll costs one poll, not the evening.** `read` is an unretried HTTPS
            # GET and `write` a PATCH; a single ReadTimeout on hotel wifi used to propagate
            # out of here, out of the command, and — under `asta room` — tear down the Rich
            # screen at 21:47. Nothing is lost by carrying on: the tracker rebuilds the whole
            # picture from the `purchases/` ledger next cycle, so a failed poll is a poll we
            # did not make and nothing more.
            #
            # Counted and said out loud, because the failure that matters is the silent one:
            # a room that looks quiet for twenty minutes while the network is gone.
            name = type(exc).__name__
            errors[name] = errors.get(name, 0) + 1
            heartbeat(f"[{cycles}] {name}: {exc}")
            sleep(poll_seconds)

    return LoopReport(cycles=cycles, bids_sent=bids_sent, refused=refused, errors=errors)


class LotRouter:
    """Read whichever node holds the live lot, and remember which one for the raise.

    Two nodes carry a lot and they are not interchangeable (``docs/fantalab/06 §10.6``, proved
    live 2026-08-28). CHIAMA random puts it on ``auction/<fl>``; ASSEGNA random puts it on
    ``assign/<fl>``. A bidder reading only ``auction/`` bids on nothing for the whole of an
    ASSEGNA-run evening — and the failure is silent, because an empty node is indistinguishable
    from a room between lots.

    ``auction/`` is preferred when both answer. Between lots it carries an ``update_type:
    reset`` and ``assign/`` briefly returns to null, so a real overlap means the called lot is
    the live one.

    ⚠ **Reading ``assign/`` is proved; writing it as a participant is not**
    (``docs/fantalab/06 §10.5``). The node travels with the lot rather than being assumed, so
    a ``401`` on that path is legible as "we cannot write here" and those lots can be bid by
    hand without the loop pretending otherwise.
    """

    def __init__(
        self,
        *,
        read: Callable[[str], Snapshot | None],
        write: Callable[[dict[str, Any], str], Any] | None = None,
    ) -> None:
        self._read = read
        self._write = write
        #: Where the last lot came from. `auction` before the first read: a raise cannot
        #: precede a lot, and a silent wrong guess would return a 401 that reads as a lost race.
        self.node = "auction"

    @staticmethod
    def _has_lot(snapshot: Snapshot | None) -> bool:
        return bool(snapshot) and isinstance((snapshot or {}).get("player_id"), str)

    def read_lot(self) -> tuple[Snapshot | None, str]:
        """The live lot and the node it came from, or ``(None, "auction")`` when there is none."""
        for node in ("auction", "assign"):
            snapshot = self._read(node)
            if self._has_lot(snapshot):
                self.node = node
                return snapshot, node
        self.node = "auction"
        return None, "auction"

    def write_raise(self, payload: dict[str, Any]) -> Any:
        """PATCH the node the lot was read from. Raises if no writer was given."""
        if self._write is None:  # pragma: no cover - a read-only router is a caller error
            raise RuntimeError("this LotRouter has no writer")
        return self._write(payload, self.node)
