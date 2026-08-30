"""The reconstruction fold, made resumable. Pure — no I/O, no clock.

`reconstruct` reads a whole evening and returns every sale. That is right for a
recording and wrong for a follower: `harvest load --follow` re-ran it over the entire
landing zone on every non-empty pass, because a window starting mid-turn rebuilds a
ladder from nothing and the upsert is `DO UPDATE`, so the short ladder would overwrite
the complete one. Measured on the real zone: 1.22 GB, 2,355,848 records, every ten
seconds.

Carrying the ladder across passes is what removes the need to re-read. This module is
that fold with its state named, so it can be checkpointed beside the byte offset.

**The state is small, which was not obvious and is the reason this is worth doing.**
The planning estimate was 161 MB — large enough that writing it each pass would have
cost more than the re-fold it replaces. Measured, it is **198 KB**, because two of the
four structures `reconstruct` keeps are not needed here:

* `seen_updates` — 2,353,995 entries and ~290 MB of RSS, guarding against 1,853
  duplicate observations (0.08%). `reconstruct`'s own docstring records that the guard
  is redundant, and it was re-verified against the recorded evening: identical output
  with and without, 11,498 assignments and 70,627 rungs either way, 2.3x faster
  without. The two rules that actually absorb duplicates — a rung only on a price
  change, and a later close superseding an earlier one — are kept.
* `sold` — the map from `(auction, player)` to an index, so a later close could replace
  an earlier one *in the returned list*. Here that is the database's job:
  `upsert_assignments` is `ON CONFLICT DO UPDATE` on `(asta_id, player_uuid)`, so
  emitting a close twice is idempotent and the later one wins.

What is left is `ladders` and `on_the_block`, bounded by auctions **in progress** rather
than by records seen: 1,282 auctions and 7,317 rungs at peak.

**Emitting a close twice is idempotent across writes and fatal within one.** The
database settles last-wins — `upsert_assignments` is `ON CONFLICT DO UPDATE` on
`(asta_id, player_uuid)` — but only *between* statements. Postgres rejects a single
statement whose `VALUES` carry the same conflict key twice: *"ON CONFLICT DO UPDATE
command cannot affect row a second time"*. `reconstruct` never hit that because its
`sold` map collapsed repeats before returning; `advance` emits every close it sees,
and the node keeps returning a closed state until the next call begins, so one window
routinely carries the same sale twice. **Always pass emitted closes through `drain`.**

`compare.equivalent` cannot catch this — its `_by_key` collapses duplicates on the way
in, so a doubled emission compares equal. The test for it counts rows.

**Emission is separate from folding, and that separation is load-bearing.** `harvest
load` defers its assignment work while catching up — see `aste/cli.py` — but it advances
the byte offset on every pass regardless. A fold that only ran when it emitted would
consume those records and never report their sales. So `advance` always folds and
returns what changed; the caller decides when to drain.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from fantabot.aste.models import Assignment, Bid
from fantabot.aste.reconstruct import BIDDING, CLOSE, FIRST_CALL, _bid

#: Bumped when the shape of a checkpointed state changes. A file written by an older
#: version is discarded rather than adapted: the fallback is a full re-fold, which is
#: correct and merely slow, and guessing at an old layout is neither.
STATE_VERSION = 1


@dataclass(frozen=True)
class FoldState:
    """Everything the fold must remember between passes.

    Frozen, and `advance` returns a new one. That is not style: it is what makes the
    failure path safe.

    **Bind the new state only after the write commits.** The caller folds, writes, then
    advances its checkpoint. If the write fails the byte offset does not move, so the
    same window is re-read — and folding it onto a state that already contains it
    appends the same rungs again. Measured: a window opening mid-ladder gives `[0,5,6,7]`
    clean and `[0,5,6,7,6,7]` replayed, a ladder that steps downwards, which
    `tests/test_aste_reconstruct.py` asserts can never happen and `reconstruct` names as
    the corruption an opponent model reads as a bidding war. Keeping the old state until
    the commit succeeds makes the retry a no-op instead.

    A window that opens on `first_call` self-heals, because the reset clears the ladder.
    That is why a fixture split there proves nothing.
    """

    #: auction -> the ladder of the turn currently on the block.
    ladders: Mapping[str, tuple[Bid, ...]] = field(default_factory=dict)
    #: auction -> the player currently on the block. `None` is a real value: a
    #: `confirm` state carries no player because the slot is empty between sales.
    on_the_block: Mapping[str, str | None] = field(default_factory=dict)
    #: Auctions this state has ever folded. An auction adopted mid-run is absent, and
    #: the caller uses that to know its history must be rebuilt rather than resumed.
    known: frozenset[str] = frozenset()

    def sees(self, auction_id: str) -> bool:
        return auction_id in self.known


def empty() -> FoldState:
    return FoldState()


def advance(
    state: FoldState, rows: Iterable[Mapping[str, Any]]
) -> tuple[FoldState, list[Assignment]]:
    """Fold `rows` into `state`; return the new state and the sales that closed in them.

    The returned assignments are *only* those closed by these records — that is the
    whole point. A caller that needs everything ever closed re-folds from the start.
    """
    ladders = {k: list(v) for k, v in state.ladders.items()}
    on_the_block = dict(state.on_the_block)
    known = set(state.known)
    closed: list[Assignment] = []

    for row in rows:
        auction_id = row.get("auction_id")
        raw = row.get("state")
        if not isinstance(auction_id, str) or not isinstance(raw, Mapping):
            continue
        known.add(auction_id)

        player_id = raw.get("player_id")
        update_type = raw.get("update_type")

        # A turn begins on `first_call`, and the same player can begin twice — an
        # annulled call puts him back on the block from zero. Keying the reset on the
        # player changing alone glued those two turns into one ladder that climbed and
        # then fell, which an ascending auction cannot produce. `_UNSEEN` is spelled
        # here as "auction not in on_the_block", which is the same test across a
        # resumed state as it was inside one pass.
        first_time = auction_id not in on_the_block
        if update_type == FIRST_CALL or first_time or on_the_block[auction_id] != player_id:
            on_the_block[auction_id] = player_id if isinstance(player_id, str) else None
            ladders[auction_id] = []

        if update_type not in BIDDING:
            continue

        rung = _bid(raw)
        ladder = ladders[auction_id]
        # A re-observation at the same price is the same offer seen twice.
        if rung is not None and (not ladder or ladder[-1].price != rung.price):
            ladder.append(rung)

        if update_type != CLOSE or not isinstance(player_id, str) or rung is None:
            continue
        closed.append(
            Assignment(
                auction_id=auction_id,
                player_id=player_id,
                price=rung.price,
                buyer_team_id=rung.team_id,
                closed_at_ms=raw.get("last_update"),
                ladder=tuple(ladder),
            )
        )

    return (
        replace(
            state,
            ladders={k: tuple(v) for k, v in ladders.items()},
            on_the_block=on_the_block,
            known=frozenset(known),
        ),
        closed,
    )


def drain(closes: Iterable[Assignment]) -> list[Assignment]:
    """One row per `(auction, player)`, last close winning. Order preserved.

    Required before every write, for two independent reasons.

    *Postgres.* A statement whose `VALUES` repeat a conflict key raises
    `ON CONFLICT DO UPDATE command cannot affect row a second time`. Under `--follow`
    that is caught by the loader's `SQLAlchemyError` handler, reported as a database
    outage, and retried against the identical window for ever.

    *Correctness.* Last wins, not first. A re-emission carries the same price so either
    rule agrees; a genuine re-auction after an annulled call does not, and `reconstruct`
    records what first-wins cost when it was tried: the superseded price on 271 pairs,
    the buyer lost on 175 of them, and the evening's spend under-counted by 1,814
    credits.
    """
    final: dict[tuple[str, str], Assignment] = {}
    for assignment in closes:
        final[(assignment.auction_id, assignment.player_id)] = assignment
    return list(final.values())


def to_json(state: FoldState) -> dict[str, Any]:
    """A checkpointable form. Plain types only, so the file is readable by a human."""
    return {
        "version": STATE_VERSION,
        "ladders": {
            auction: [[b.price, b.team_id, b.at_ms] for b in ladder]
            for auction, ladder in state.ladders.items()
        },
        "on_the_block": dict(state.on_the_block),
        "known": sorted(state.known),
    }


def from_json(blob: Any) -> FoldState | None:
    """Rebuild a state, or `None` if the file is unusable.

    `None` means "re-fold from the beginning", which is correct and merely slow. That
    is the only safe answer to a state written by an older version, a truncated file or
    anything that is not the shape below — adapting a layout we no longer understand
    would put a wrong ladder into an append-only archive.
    """
    if not isinstance(blob, Mapping) or blob.get("version") != STATE_VERSION:
        return None
    try:
        ladders = {
            str(auction): tuple(Bid(price=int(p), team_id=t, at_ms=at) for p, t, at in rungs)
            for auction, rungs in blob["ladders"].items()
        }
        on_the_block = {str(k): (str(v) if v is not None else None)
                        for k, v in blob["on_the_block"].items()}
        known = frozenset(str(a) for a in blob["known"])
    except (KeyError, TypeError, ValueError):
        return None
    return FoldState(ladders=ladders, on_the_block=on_the_block, known=known)
