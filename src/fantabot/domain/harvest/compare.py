"""Comparing a polled capture against a streamed one. Pure.

T16's criterion is **strict superset**, and the strictness is the point. Both
collectors watch the same rooms, so they must agree on every sale — and the
streamed one must additionally carry rungs the poller could not see, because a
poll reads a merged snapshot and two raises inside one interval collapse into
one observation.

**Equality is a failure.** If streaming reproduces exactly what polling
produced, the subscription bought nothing and the reducer is the first thing to
suspect, not the last. A comparison that accepted equality would pass a broken
reducer and retire the working collector on the strength of it.

Three asymmetries are deliberate:

* a sale polling saw and streaming missed is a **regression**, and no number of
  extra rungs excuses it;
* a sale only streaming saw is **expected** — the two processes start and stop on
  their own schedules — so it is counted, not faulted;
* a disagreement on price means one of them is reading the node wrong, and the
  two captures cannot be pooled until it is known which.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from fantabot.domain.harvest.models import Assignment

Key = tuple[str, str]


@dataclass
class Verdict:
    """The outcome of one comparison, and enough detail to act on it."""

    ok: bool
    reason: str = ""
    shared: int = 0
    only_streamed: int = 0
    only_polled: list[Key] = field(default_factory=list)
    extra_rungs: int = 0
    outside_window: int = 0

    def summary(self) -> str:
        if self.ok:
            return (
                f"strict superset · {self.shared} shared sales · "
                f"{self.extra_rungs} rungs polling could not see · "
                f"{self.only_streamed} sales only streaming caught"
                + (f" · {self.outside_window} outside the shared window" if self.outside_window else "")
            )
        return f"not a superset — {self.reason}"


def _by_key(assignments: Sequence[Assignment]) -> dict[Key, Assignment]:
    return {(a.auction_id, a.player_id): a for a in assignments}


def observation_window(
    polled_rows: Sequence[Mapping[str, object]],
    streamed_rows: Sequence[Mapping[str, object]],
) -> tuple[int, int] | None:
    """The interval during which **both** collectors were watching.

    Derived from the captures' ``seen_at``, not from any assignment's close time.
    That distinction was learned the expensive way: a close is a server event and
    the node keeps returning the closed state until the next call begins, so
    either side can observe a sale that happened before it connected. Bounding by
    close times therefore excused nothing — the poller's first read caught two
    closes timestamped inside the window it had just derived.

    Both sides are required. One empty capture has no overlap with anything.
    """
    stamps = []
    for rows in (polled_rows, streamed_rows):
        seen = [str(r["seen_at"]) for r in rows if r.get("seen_at")]
        if not seen:
            return None
        stamps.append((min(seen), max(seen)))
    start = max(datetime.fromisoformat(s).timestamp() for s, _ in stamps)
    end = min(datetime.fromisoformat(e).timestamp() for _, e in stamps)
    return int(start * 1000), int(end * 1000)


def shared_window(
    polled: Sequence[Assignment], streamed: Sequence[Assignment]
) -> tuple[int, int] | None:
    """The interval both captures were watching.

    Two processes cannot start on the same millisecond, and the first shadow run
    made the cost of ignoring that concrete: the poller led by ten seconds, caught
    three closes before the streamer had connected, and the comparison reported
    three lost sales. It was right to — it cannot know why — but the answer is to
    compare like with like.
    """
    stamps = [
        [a.closed_at_ms for a in side if a.closed_at_ms is not None]
        for side in (polled, streamed)
    ]
    if not all(stamps):
        return None
    return max(min(s) for s in stamps), min(max(s) for s in stamps)


def compare(
    polled: Sequence[Assignment],
    streamed: Sequence[Assignment],
    window: tuple[int, int] | None = None,
) -> Verdict:
    """Is ``streamed`` a strict superset of ``polled``, within the shared window?

    The window narrows *what is compared* and never softens the verdict: a sale
    lost inside it is still a failure.
    """
    if not streamed:
        # Nothing is not a superset of anything, including nothing. A run that
        # captured no sales has demonstrated nothing about the reducer.
        return Verdict(ok=False, reason="the streamed capture holds no sales")

    bounds = window if window is not None else shared_window(polled, streamed)
    outside = 0
    if bounds is not None:
        start, end = bounds

        def inside(a: Assignment) -> bool:
            return a.closed_at_ms is None or start <= a.closed_at_ms <= end

        outside = sum(1 for a in polled if not inside(a))
        polled = [a for a in polled if inside(a)]
        streamed = [a for a in streamed if inside(a)]

    left, right = _by_key(polled), _by_key(streamed)

    missing = sorted(set(left) - set(right))
    if missing:
        shown = ", ".join(player for _auction, player in missing[:5])
        return Verdict(
            ok=False,
            reason=f"{len(missing)} sale(s) polling caught and streaming lost: {shown}",
            only_polled=missing,
            outside_window=outside,
        )

    shared = sorted(set(left) & set(right))
    for key in shared:
        if left[key].price != right[key].price:
            return Verdict(
                ok=False,
                reason=(
                    f"price disagreement on {key[1]}: "
                    f"polled {left[key].price}, streamed {right[key].price}"
                ),
            )

    extra = sum(max(0, len(right[k].ladder) - len(left[k].ladder)) for k in shared)
    if extra == 0:
        return Verdict(
            ok=False,
            reason=(
                "no rung was gained — streaming saw exactly what polling saw, "
                "which is what a broken reducer also produces"
            ),
            shared=len(shared),
        )

    return Verdict(
        ok=True,
        shared=len(shared),
        only_streamed=len(set(right) - set(left)),
        extra_rungs=extra,
        outside_window=outside,
    )


def equivalent(whole_file: Sequence[Assignment], incremental: Sequence[Assignment]) -> Verdict:
    """Do two reconstructions of the same records agree exactly?

    **A different question from `compare`, over the same machinery.** `compare` asks
    whether streaming is a *strict superset* of polling, and treats equality as a
    failure — two collectors watching the same rooms must agree on every sale, and
    the streamed side must additionally carry rungs a merged snapshot could not show.
    Here both sides read the *same* records by two routes, so equality is the only
    acceptable answer and any difference is a defect in one of them.

    That is why this exists rather than `compare(a, b) and compare(b, a)`: those two
    calls can never both succeed, because each demands the other side gained a rung.

    Written for the incremental reducer. A fold that keeps state across passes must
    produce exactly what one whole-file pass produces, and "exactly" has to include
    the ladders — a window starting mid-turn rebuilds a ladder from nothing, and the
    upsert is DO UPDATE, so a short ladder silently overwrites a complete one. The
    price alone would not notice that.
    """
    left, right = _by_key(whole_file), _by_key(incremental)

    missing = sorted(set(left) - set(right))
    if missing:
        shown = ", ".join(player for _auction, player in missing[:5])
        return Verdict(
            ok=False,
            reason=f"{len(missing)} sale(s) the whole-file fold found and the incremental lost: {shown}",
            only_polled=missing,
        )

    invented = sorted(set(right) - set(left))
    if invented:
        shown = ", ".join(player for _auction, player in invented[:5])
        return Verdict(
            ok=False,
            reason=f"{len(invented)} sale(s) only the incremental fold produced: {shown}",
            only_streamed=len(invented),
        )

    for key in sorted(left):
        a, b = left[key], right[key]
        if a.price != b.price:
            return Verdict(
                ok=False,
                reason=f"price disagreement on {key[1]}: whole-file {a.price}, incremental {b.price}",
            )
        if a.buyer_team_id != b.buyer_team_id:
            return Verdict(
                ok=False,
                reason=f"buyer disagreement on {key[1]}: {a.buyer_team_id} vs {b.buyer_team_id}",
            )
        if a.ladder != b.ladder:
            return Verdict(
                ok=False,
                reason=(
                    f"ladder disagreement on {key[1]}: whole-file {len(a.ladder)} rungs, "
                    f"incremental {len(b.ladder)} — a short ladder overwrites a complete one"
                ),
            )

    return Verdict(
        ok=True,
        shared=len(left),
        # `summary()` renders `compare`'s question — "strict superset", "rungs polling
        # could not see" — which is the wrong sentence for this one. Saying so here
        # keeps a passing equivalence from reading like a passing superset check.
        reason=f"identical: {len(left)} sales, same prices, buyers and ladders",
    )
