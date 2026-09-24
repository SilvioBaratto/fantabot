"""A Classic room drives the whole RoomTracker cycle: fold ledger, plan, price, decide.

C3 tested the format-dispatched pieces (schemi_open, drop_unvaluable, the bargain gate) in
isolation; this proves they compose — a Classic pool + ClassicRosterRules go through
`cycle` end to end and produce a frame without touching a Mantra-only path.
"""

from __future__ import annotations

from fantabot.application.asta_room import RoomTracker
from fantabot.domain.asta.bid import Seat
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.value import NaiveValueModel
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules

POOL = [
    ClassicPlayer("1", "P"), ClassicPlayer("2", "P"),
    ClassicPlayer("3", "D"), ClassicPlayer("4", "D"),
    ClassicPlayer("5", "C"), ClassicPlayer("6", "C"),
    ClassicPlayer("7", "A"), ClassicPlayer("8", "A"),
]
RULES = ClassicRosterRules(size=4, bands=(("P", 1, 1), ("D", 1, 1), ("C", 1, 1), ("A", 1, 1)))
TEAMS = {p.id: p.id for p in POOL}
PRICES = {p.id: 10.0 for p in POOL}
NAMES = {p.id: f"N{p.id}" for p in POOL}
VALUE = NaiveValueModel(
    signals={p.id: float(9 - int(p.id)) for p in POOL},
    prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
)
BRIDGE = {"lot-c": 5}  # uuid -> fantacalcio id 5, a midfielder


def _tracker(ledger=(), **kw):  # type: ignore[no-untyped-def]
    return RoomTracker(
        seat=Seat(fantateam_id="us", user_id="me"),
        bridge=BRIDGE, pool=POOL, value=VALUE, prices=PRICES, teams=TEAMS,
        legality={}, names=NAMES, rules=RULES, budget=100.0, lam=0.0,
        ledger=lambda: list(ledger), journal=lambda _row: None,
        counter_time=10, counter_time_first=20, **kw,
    )


def _lot(uuid: str = "lot-c", price: int = 5) -> dict[str, object]:
    return {"player_id": uuid, "price": price, "user_id": "rival", "last_bid_time": 0}


def test_a_classic_lot_is_priced_end_to_end() -> None:
    frame = _tracker().cycle(_lot(), now_ms=1_000)

    assert frame.lot_name == "N5"
    assert set(frame.walkaways) <= {p.id for p in POOL}  # every priced target is a real player
    assert frame.max_cap >= 0  # the Classic 25... here 4-slot cap arithmetic did not crash


#: A rosa that fields exactly one Classic module (451), so `schemi_open` can be a number the
#: Mantra branch is unable to produce. The four-slot `POOL` above cannot: with an empty
#: `owned` both branches of `cycle`'s `isinstance(rules, ClassicRosterRules)` return 0 —
#: `fieldable_formations({})` is empty and `fieldable_schemi([], legality={})` is too — so the
#: assertion that used to stand here held with the Classic branch deleted outright, measured
#: 2026-09-24 against the whole default tier.
XI_ROLES = ["P"] + ["D"] * 4 + ["C"] * 5 + ["A"]
XI_POOL = [ClassicPlayer(str(i), role) for i, role in enumerate(XI_ROLES, start=1)]
XI_RULES = ClassicRosterRules(
    size=11, bands=(("P", 1, 1), ("D", 4, 5), ("C", 5, 5), ("A", 1, 2))
)


def test_schemi_open_counts_classic_formations_not_schemi() -> None:
    """1, not 0: a 1-4-5-1 rosa fields the 451 module and nothing else.

    The Mantra branch would answer 0 for this same rosa — `fieldable_schemi` keeps only
    `MantraPlayer`s and every one of these is a `ClassicPlayer` — so the number itself is
    what says which branch ran.
    """
    tracker = RoomTracker(
        seat=Seat(fantateam_id="us", user_id="me"),
        bridge={f"u{p.id}": int(p.id) for p in XI_POOL},
        pool=XI_POOL,
        value=NaiveValueModel(
            signals={p.id: 1.0 for p in XI_POOL},
            prior_mean=1.0, base_variance=1.0, no_history_variance=1.0,
        ),
        prices={p.id: 1.0 for p in XI_POOL}, teams={p.id: p.id for p in XI_POOL},
        legality={}, names={p.id: f"N{p.id}" for p in XI_POOL}, rules=XI_RULES,
        budget=100.0, lam=0.0,
        ledger=lambda: [AssignmentEvent(f"u{p.id}", 1, "us") for p in XI_POOL],
        journal=lambda _row: None, counter_time=10, counter_time_first=20,
    )

    frame = tracker.cycle(None, now_ms=1_000)

    assert len(frame.owned) == 11
    assert frame.schemi_open == 1


def test_a_won_classic_lot_folds_into_owned() -> None:
    frame = _tracker(ledger=[AssignmentEvent("lot-c", 7, "us")]).cycle(None, now_ms=1_000)
    assert "5" in frame.owned  # the ledger sale mapped uuid->id and became ours
