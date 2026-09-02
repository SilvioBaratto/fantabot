"""A pasted link and a stored uid become a room we can bid in.

The fetch is injected. This layer never opens a socket, never sees the Bearer, and never
touches Postgres — the interface binds `rest.fetch_league` with the token and hands the body
in. `test_the_module_is_structurally_unable_to` proves the last of those with the import graph
rather than trusting the docstring, which is why `PlanInputs` had to move to a leaf module
first: anything defined beside `read_plan_inputs` carries a path to persistence with it.

⚠ **`rest.fetch_league` has never run against a real room.** It had no caller in `src/` at all
before this phase — only tests. The shapes here are what `docs/fantalab/06 §3` records as
observed; the first live `--resolve-only` is what confirms them.

**Three rooms are refused rather than entered**, each because bidding in one would be wrong in
a way the platform will not tell us about:

* not Mantra — `domain/asta` is Mantra only, and a Classic room has no schema matrix to check
  a rosa against;
* `raise_mode: ordered` — the `raise_state` array such a room expects is undecoded
  (`docs/fantalab/06 §8`), so our payload would be malformed, and a malformed raise returns
  the same `401` as a lost race;
* unseated — bidding is unauthenticated and the server validates no identity, so a wrong seat
  is accepted with a `200` and drives somebody else's team all evening.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fantabot.adapters.http.fantalab.rest import RoomConfig
from fantabot.adapters.http.fantalab.rest import Seat as RoomSeat
from fantabot.domain.asta.bid import Seat as BidSeat
from fantabot.domain.asta.bid import decide_bid, max_bid, pass_reason
from fantabot.domain.asta.legality import SchemaLegality, fieldable_schemi
from fantabot.domain.asta.live import AssignmentEvent, resolve_ids, seconds_left
from fantabot.domain.asta.opponents import MIN_BID
from fantabot.domain.asta.optimizer import InfeasibleRoster
from fantabot.domain.asta.reservation import (
    BARGAIN_BETA,
    BARGAIN_BUDGET_SHARE,
    BARGAIN_MIN_BOOK,
    apply_event,
    bargain_allowance,
    lot_ceiling,
    opportunistic_walkaway,
    reservations,
)
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import AstaState, RosterRules, drop_unvaluable
from fantabot.domain.asta.value import ValueModel

#: How many sales travel in a copilot brief and on the frame. Three is the room's tempo
#: without being a transcript.
RECENT_SALES = 3


class RoomRefused(RuntimeError):
    """This room cannot be bid in, and the message says what the operator can do about it."""


@dataclass(frozen=True)
class ResolvedRoom:
    """Everything the live loop needs, and nothing that could leak a credential."""

    fantaleague_id: str
    db: int | None
    seat: RoomSeat
    num_teams: int | None
    num_credits: int | None
    min_player: int | None
    max_player: int | None
    asta_mode: str | None
    raise_mode: str | None
    counter_time: int | None
    counter_time_first: int | None
    call_at_quotaz: bool
    #: `fantateam_id -> team name`, so a rival reads as a name rather than a uuid. Uuids are
    #: unreadable at speed, and the screen is read at speed or not at all.
    team_names: Mapping[str, str]

    @property
    def budget(self) -> float:
        """The room's credits, or FantaLab's default when it does not say."""
        return float(self.num_credits if self.num_credits is not None else 500)


# Two `Seat` types are in play and they are not the same thing. `rest.Seat` is a chair in the
# room — it carries a team name, a position and a per-seat credit override. `bid.Seat` is the
# pair of ids a raise payload has to carry. Aliased rather than reconciled, because collapsing
# them would put a rendering concern inside the payload the platform validates.


def resolve_room(
    fantaleague_id: str,
    *,
    user_id: str,
    fetch: Callable[[str], RoomConfig],
) -> ResolvedRoom:
    """Fetch the room and check we can legitimately bid in it. Raises `RoomRefused` if not.

    Takes an already-parsed `RoomConfig`: `rest.fetch_league` does the parsing and has its own
    tests for it, and re-parsing here would give two places to disagree about a field.
    """
    config = fetch(fantaleague_id)

    if config.asta_type and config.asta_type != "mantra":
        raise RoomRefused(
            f"this room is {config.asta_type}, not mantra. Nothing here can field a Classic "
            "rosa — domain/asta is Mantra only, twelve role codes across eleven schemi."
        )

    if config.raise_mode and config.raise_mode != "free":
        raise RoomRefused(
            f"raise_mode is {config.raise_mode!r}, not 'free'. The raise_state array an "
            "ordered room expects is undecoded (docs/fantalab/06 §8), so our payload would be "
            "malformed — and a malformed raise returns the same 401 as a lost race, so we "
            "would not even be able to tell. Bid this room by hand."
        )

    seat = config.seat_of(user_id)
    if seat is None:
        free = ", ".join(s.team_name or s.fantateam_id for s in config.free_seats())
        raise RoomRefused(
            "we hold no seat in this room. Claim one in the browser first — bidding is "
            "unauthenticated and the server validates no identity, so driving a seat we do "
            f"not own would be accepted with a 200. Free seats: {free or '(none)'}."
        )

    return ResolvedRoom(
        fantaleague_id=config.fantaleague_id,
        db=config.db,
        seat=seat,
        num_teams=config.num_teams,
        num_credits=config.num_credits,
        min_player=config.min_player,
        max_player=config.max_player,
        asta_mode=config.asta_mode,
        raise_mode=config.raise_mode,
        counter_time=config.counter_time,
        counter_time_first=config.counter_time_first,
        call_at_quotaz=config.call_at_quotaz,
        team_names={s.fantateam_id: s.team_name or s.fantateam_id for s in config.seats},
    )


@dataclass(frozen=True)
class RoomFrame:
    """One cycle, as a value. Everything the screen draws, and nothing it has to ask for.

    Frozen because the renderer must not be able to change what it is drawing, and because a
    frame that survives a failed cycle is what lets the screen hold the last good picture
    instead of blanking.
    """

    #: The lot as the node names it — a FantaLab uuid, which is what a bid payload must carry.
    lot_id: str | None
    #: The same lot as a human reads it. A uuid is unreadable at speed.
    lot_name: str | None
    price: int
    high_bidder: str | None
    seconds_left: float | None
    #: `auction` or `assign`: which node this lot was read from, and the one a raise must go
    #: back to (`docs/fantalab/06 §10.6`).
    node: str
    target: str | None
    walk_away: int | None
    #: `marginal`, `floor` or `budget` — which of the three terms in
    #: `min(budget, max(marginal, floor))` produced the number — or `bargain`, which is a
    #: different thing entirely: a lot the plan never named, whose ceiling is the highest
    #: price at which re-solving the rosa with him in it still beats the plan.
    #: Shown beside the number, because a fused figure is one nobody can argue with.
    provenance: str | None
    #: `waiting` | `hold` | `pass` | `bid`
    decision: str
    #: The guard that bound, when the decision was `pass`.
    reason: str | None
    #: A line for the quiet failures that otherwise read as ordinary passes.
    note: str | None
    credits_left: int
    max_cap: int
    owned: tuple[str, ...]
    plan: tuple[str, ...]
    #: Sales the listone could not name. Each is a purchase we never subtracted, so a rival's
    #: budget and that player's availability are both wrong until it is explained.
    unresolved_sales: int
    #: Every walk-away this cycle priced, keyed by fantacalcio id. The LISTONE pane's column:
    #: seeing only the lot's own number tells the operator nothing about what is coming.
    walkaways: Mapping[str, float]
    #: How many of the eleven schemi the rosa can field as it stands. Computed here, where the
    #: legality matrix already is — the room's wiring used to pass a literal 0 into every
    #: copilot brief, so each one opened by telling the model something false about our rosa.
    schemi_open: int
    #: The last few sales as `name price buyer`, the room's tempo in three lines. Same reason:
    #: the brief claimed nothing had been sold, for the whole evening.
    recent: tuple[str, ...]
    #: Credits already gone on lots the plan never named, and the evening's ceiling for them.
    #: On the frame because an aggregate cap the operator cannot see is a cap he finds out
    #: about by not understanding why a bid did not go in. Defaulted so the renderer's own
    #: fixtures — and any frame built before this existed — still construct.
    bargain_spent: int = 0
    bargain_allowance: int = 0


class RoomTracker:
    """Fold the ledger, re-plan, decide — and return the whole picture rather than one tuple.

    The closure this replaces computed every one of these facts and returned two of them, so
    the screen had nothing to draw. `asta bid` now consumes the same frame: a second copy of
    this logic is precisely the drift `CLAUDE.md` records, when three commands each grew their
    own value model and the one that spent credits fell behind the one that advised.

    Primitives rather than `PlanInputs`, deliberately — see the module docstring.
    """

    def __init__(
        self,
        *,
        seat: BidSeat,
        bridge: Mapping[str, int],
        pool: Sequence[MantraPlayer],
        value: ValueModel,
        prices: Mapping[str, float],
        teams: Mapping[str, str],
        legality: dict[str, SchemaLegality],
        names: Mapping[str, str],
        rules: RosterRules,
        budget: float,
        lam: float,
        floor: Callable[[str], float] | None,
        ledger: Callable[[], Iterable[AssignmentEvent]],
        journal: Callable[[Mapping[str, Any]], None],
        counter_time: int | None,
        counter_time_first: int | None,
        step: int = 1,
        bargain_beta: float = BARGAIN_BETA,
        bargain_min_book: int = BARGAIN_MIN_BOOK,
        bargain_share: float = BARGAIN_BUDGET_SHARE,
    ) -> None:
        self._seat = seat
        self._bridge = bridge
        self._pool = pool
        self._value = value
        self._prices = prices
        self._teams = teams
        self._legality = legality
        self._names = names
        self._rules = rules
        self._budget = budget
        self._lam = lam
        self._floor = floor
        self._ledger = ledger
        self._journal = journal
        self._counter_time = counter_time
        self._counter_time_first = counter_time_first
        self._step = step
        self._bargain_beta = bargain_beta
        self._bargain_min_book = bargain_min_book
        self._bargain_share = bargain_share
        # **How the tracker tells a bargain win from a planned one.** Every fantacalcio id we
        # have ever actually raised on under `bargain` provenance, for the life of the
        # process. A win is then the intersection of this set with what the ledger says we
        # own — the ledger carries a price and a buyer and nothing about *why* we bid, and it
        # is the only record of a purchase, so the provenance has to be remembered here and
        # joined to it. A lot we bid on and lost never enters `owned` and so costs nothing.
        #
        # ⚠ It is in memory, so a restart mid-evening forgets what has been spent and hands
        # the cap back its full allowance. Journaling the provenance and reading it back would
        # fix that and would put persistence on the decision path; the cap is a guard rail on
        # an opt-in path that is off by default, and this is the cheaper half of that trade.
        self._bargain_wins: set[str] = set()
        # One slot, keyed on the state its answers were computed against — the same shape as
        # `latest` and `screen` in the room's wiring, for the same reason. A bargain ceiling
        # is a function of the rosa, the purse and the taken set, and of nothing that moves
        # between polls; a lot sits on the block for 20-60 s at a 2 s poll, so without this
        # the room would pay for the same re-solve thirty times and get the same number.
        # The key changes when a sale lands, which is the moment the lot ends anyway.
        self._bargain_key: tuple[AstaState, RosterRules] | None = None
        self._bargains: dict[str, int] = {}

    def cycle(
        self, snapshot: Mapping[str, Any] | None, *, now_ms: int, node: str = "auction"
    ) -> RoomFrame:
        """One poll: fold, re-plan, decide, journal, and return the frame."""
        state = AstaState(total_budget=self._budget)
        events, unresolved = resolve_ids(self._ledger(), self._bridge)
        for event in events:
            state = apply_event(state, event, our_team_id=self._seat.fantateam_id)

        # A player we won whom the pool cannot name would make the optimiser refuse this state
        # — and refuse it again next cycle, because the ledger is re-read and a purchase is
        # never withdrawn. Setting him aside is what makes a hold last one lot.
        state, rules, unvaluable = drop_unvaluable(state, self._pool, self._rules)

        credits_left = int(state.remaining_budget)
        cap = max_bid(credits_left, rules.size - len(state.owned))

        owned_players = [p for p in self._pool if p.id in set(state.owned)]
        schemi_open = len(fieldable_schemi(owned_players, self._legality))
        # Three, not the whole ledger: a brief carrying two hundred lines is a brief nobody
        # reads, the model included.
        recent = tuple(
            f"{self._names.get(e.player_id, e.player_id)} {e.price} a {e.buyer_team_id}"
            for e in events[-RECENT_SALES:]
        )

        # A rosa that cannot be completed from here is a real state, not an error: too few
        # credits for the slots left, or a band no remaining player can fill. `drop_unvaluable`
        # handles the id we cannot name; this handles the arithmetic. Either way the loop must
        # keep drawing and keep holding — raising out of a cycle would end the evening on a
        # condition the next sale might undo.
        try:
            plan, walkaways = reservations(
                state, self._pool, value=self._value, prices=self._prices, teams=self._teams,
                legality=self._legality, rules=rules, lam=self._lam, n_targets=None,
                floor=self._floor,
            )
            planned: tuple[str, ...] = plan.optimal.player_ids
            # The number every bargain is judged against. `None` when there is no plan: a
            # rosa that cannot be completed has no objective to beat, and "better than
            # nothing" is not a reason to spend.
            baseline: float | None = plan.optimal.objective
        except InfeasibleRoster as exc:
            walkaways = {}
            planned = ()
            baseline = None
            unvaluable = [*unvaluable, f"no completable rosa from here: {exc}"]

        if self._bargain_key != (state, rules):
            self._bargain_key = (state, rules)
            self._bargains = {}

        # The join between "why we bid" (remembered) and "what we bought" (the ledger). Read
        # off the ledger rather than accumulated as we go, for the reason every other number
        # in this loop is: the ledger is re-read whole every cycle and is the only record that
        # survives a lost poll, so a counter incremented on our own optimism would drift.
        held = set(state.owned)
        bargain_spent = int(
            sum(
                e.price
                for e in events
                if e.buyer_team_id == self._seat.fantateam_id
                and e.player_id in self._bargain_wins
                and e.player_id in held
            )
        )
        allowance = bargain_allowance(
            self._budget, bargain_spent, share=self._bargain_share
        )

        frame = self._decide(
            snapshot, now_ms=now_ms, node=node, state=state, walkaways=walkaways,
            plan=planned, credits_left=credits_left, cap=cap,
            schemi_open=schemi_open, recent=recent, owned_players=owned_players, rules=rules,
            unresolved=len(unresolved), unvaluable=unvaluable, baseline=baseline,
            bargain_spent=bargain_spent, allowance=allowance,
        )
        self._journal(
            {
                "at_ms": now_ms, "node": frame.node, "lot": frame.lot_id,
                "name": frame.lot_name, "price": frame.price,
                "walk_away": frame.walk_away, "provenance": frame.provenance,
                "decision": frame.decision, "reason": frame.reason,
                "credits_left": frame.credits_left, "max_cap": frame.max_cap,
                "owned": list(frame.owned),
                # An aggregate cap that leaves no record is one nobody can audit after the
                # evening — and `provenance` alone says why *this* lot was priced, never what
                # the evening has already committed to lots the plan never named.
                "bargain_spent": frame.bargain_spent,
                "bargain_allowance": frame.bargain_allowance,
            }
        )
        return frame

    def _bargain(
        self,
        player_id: str,
        *,
        state: AstaState,
        rules: RosterRules,
        baseline: float,
        hard_cap: int,
    ) -> int:
        """`lot_ceiling`, computed once per player per state. 0 means hold.

        The ceiling does not depend on the lot's current price, only on the state — which is
        exactly why it can be cached across the thirty polls one lot lives for, and why the
        cache is keyed on the state rather than aged out on a clock this layer must not read.
        `cycle` clears it when the state moves.
        """
        hit = self._bargains.get(player_id)
        if hit is not None:
            return hit
        ceiling = lot_ceiling(
            state, self._pool, value=self._value, prices=self._prices, teams=self._teams,
            legality=self._legality, rules=rules, lam=self._lam, baseline=baseline,
            player_id=player_id, hard_cap=hard_cap,
        )
        self._bargains[player_id] = ceiling
        return ceiling

    def _bargain_for(
        self,
        player_id: str,
        *,
        state: AstaState,
        rules: RosterRules,
        baseline: float | None,
        plan: tuple[str, ...],
        owned_players: Sequence[MantraPlayer],
        cap: int,
        allowance: int,
        bargain_spent: int,
    ) -> tuple[int, str | None]:
        """The opportunistic ceiling for a lot the plan did not name, and a line saying why.

        The plan not naming a lot is not a decision to let it go at *any* price: the optimizer
        rejected him at his book price and said nothing about him at a third of it. Three
        gates, in increasing order of what they cost:

        * `allowance` — the evening's aggregate cap on unplanned spend, and the only one of
          the three that looks at the other bargains. Each of the others judges this lot
          against the plan *alone*, and "better than the plan" does not compose: two lots that
          each improve the rosa can, bought together, leave a purse that buys neither of the
          players the second re-solve assumed we would still afford. Free, so it goes first;
        * `opportunistic_walkaway` — dict lookups and one bipartite match, no solve;
        * `lot_ceiling` — re-solves, and answers the only question that can justify
          spending, which is whether the rosa is *better* with him in it.

        **Nothing here may raise.** An exception inside a cycle ends the evening, and this is
        the newest and least-exercised path in the room; a bargain we failed to price is a
        bargain we do not take, which is exactly the pre-feature behaviour.
        """
        # The beta switch comes first and silently. It is the shipped default (`0.00`), so
        # anything below it would put a line on the screen for every unplanned lot of an
        # evening in which the feature is not even on.
        if baseline is None or self._bargain_beta <= 0.0:
            return 0, None
        if allowance < MIN_BID:
            return 0, (
                f"unplanned lot; the evening's bargain purse is spent "
                f"({bargain_spent}/{int(self._bargain_share * self._budget)} credits, "
                f"{int(self._bargain_share * 100)}% of {self._budget:.0f})"
            )
        try:
            lot_player = next((p for p in self._pool if p.id == player_id), None)
            if lot_player is None:
                return 0, None
            pre_gate = opportunistic_walkaway(
                lot_player, owned_players=owned_players, prices=self._prices,
                plan=plan, owned=state.owned, legality=self._legality, rules=rules,
                max_cap=cap, beta=self._bargain_beta, min_book=self._bargain_min_book,
            )
            if pre_gate is None:
                return 0, None
            hard_cap = min(pre_gate, allowance)
            if hard_cap < MIN_BID:
                return 0, None
            ceiling = self._bargain(
                player_id, state=state, rules=rules, baseline=baseline, hard_cap=hard_cap,
            )
        except Exception as exc:  # a hold, never an end to the evening
            return 0, f"bargain check failed, holding: {exc}"
        if not ceiling:
            return 0, None
        capped = " (aggregate cap)" if allowance < pre_gate else ""
        return ceiling, (
            f"not in the plan; re-solve says {ceiling} beats it (cap {hard_cap} at "
            f"{self._bargain_beta:.2f} x book{capped}; {allowance} of the evening's "
            f"bargain allowance left)"
        )

    def _decide(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        now_ms: int,
        node: str,
        state: AstaState,
        walkaways: Mapping[str, float],
        plan: tuple[str, ...],
        credits_left: int,
        cap: int,
        unresolved: int,
        unvaluable: list[str],
        baseline: float | None,
        schemi_open: int,
        recent: tuple[str, ...],
        owned_players: Sequence[MantraPlayer],
        rules: RosterRules,
        bargain_spent: int,
        allowance: int,
    ) -> RoomFrame:
        note = (
            f"{len(unvaluable)} owned player(s) we cannot value; roster band shrunk"
            if unvaluable else None
        )
        common = {
            "node": node, "credits_left": credits_left, "max_cap": cap,
            "owned": tuple(state.owned), "plan": plan, "unresolved_sales": unresolved,
            "walkaways": walkaways, "schemi_open": schemi_open, "recent": recent,
            "bargain_spent": bargain_spent, "bargain_allowance": allowance,
        }

        lot = snapshot.get("player_id") if snapshot else None
        if not isinstance(lot, str):
            return RoomFrame(
                lot_id=None, lot_name=None, price=0, high_bidder=None, seconds_left=None,
                target=None, walk_away=None, provenance=None, decision="waiting",
                reason=None, note=note, **common,  # type: ignore[arg-type]
            )

        fantacalcio_id = self._bridge.get(lot)
        price = snapshot.get("price") if snapshot else 0
        clean_price = price if isinstance(price, int) and not isinstance(price, bool) else 0
        base = {
            "lot_id": lot,
            "lot_name": self._names.get(str(fantacalcio_id)) if fantacalcio_id else None,
            "price": clean_price,
            "high_bidder": (snapshot or {}).get("user_id"),
            "seconds_left": seconds_left(
                snapshot, now_ms=now_ms,
                counter_time=self._counter_time, counter_time_first=self._counter_time_first,
            ),
            **common,
        }

        if fantacalcio_id is None:
            return RoomFrame(
                target=None, walk_away=None, provenance=None, decision="hold", reason=None,
                note="lot is not in the listone; we cannot value it",
                **base,  # type: ignore[arg-type]
            )

        raw = walkaways.get(str(fantacalcio_id))
        provenance: str | None = None
        if raw is None:
            bargain, bargain_note = self._bargain_for(
                str(fantacalcio_id), state=state, rules=rules, baseline=baseline, plan=plan,
                owned_players=owned_players, cap=cap, allowance=allowance,
                bargain_spent=bargain_spent,
            )
            if not bargain:
                return RoomFrame(
                    target=None, walk_away=None, provenance=None, decision="hold",
                    reason=None, note=note or bargain_note,
                    **base,  # type: ignore[arg-type]
                )
            raw = float(bargain)
            provenance = "bargain"
            # The LISTONE column and the copilot brief both read `walkaways`. A BID on a lot
            # whose own row shows no ceiling is the one line the operator cannot check.
            base["walkaways"] = {**walkaways, str(fantacalcio_id): raw}
            note = note or bargain_note

        walk_away = int(raw)
        # `reservations` returns min(remaining_budget, max(marginal, floor)), so three things
        # can bind and the label has to say which. Saying "floor" on a number the budget
        # decided is a lie on the one line read before spending.
        floored = self._floor(str(fantacalcio_id)) if self._floor else 0.0
        if provenance is not None:
            pass
        elif raw >= credits_left and floored > credits_left:
            provenance = "budget"
        elif floored >= raw:
            provenance = "floor"
        else:
            provenance = "marginal"
        payload = decide_bid(
            snapshot or {}, self._seat, "", target=lot, walk_away=walk_away,
            remaining_budget=credits_left, now_ms=now_ms, step=self._step, max_cap=cap,
        )
        if payload is None:
            reason = pass_reason(
                snapshot or {}, self._seat, target=lot, walk_away=walk_away,
                remaining_budget=credits_left, now_ms=now_ms, step=self._step, max_cap=cap,
            )
            return RoomFrame(
                target=lot, walk_away=walk_away, provenance=provenance, decision="pass",
                reason=reason or "none", note=note, **base,  # type: ignore[arg-type]
            )
        if provenance == "bargain":
            # Recorded on the raise and not on the ceiling: pricing a lot costs nothing and
            # happens for every unplanned lot that survives the gates, while *raising* is the
            # only act that can end with the player in our rosa. A lot we bid on and lost
            # never enters `owned`, so it never counts against the cap.
            self._bargain_wins.add(str(fantacalcio_id))
        return RoomFrame(
            target=lot, walk_away=walk_away, provenance=provenance, decision="bid",
            reason=None, note=note, **base,  # type: ignore[arg-type]
        )
