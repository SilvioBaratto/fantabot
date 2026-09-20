"""`asta live`'s fold, lifted so the app can drive it instead of building a second one.

3.10. The command was one of the last Typer bodies holding a decision the app also needs:
resolve the ledger's uuids against the listone, read the world, fold `rolling_advisory` over
every sale, and hand back the target roster with a walk-away each and what every rival has
left. `CLAUDE.md` records where the second copy goes — `GET /asta/plan` built a plan
differing from `asta optimize`'s in ten inputs — and this one would have differed in the same
place, because `callable_ids` and the corpus shape are exactly the arguments a second caller
forgets.

**The replay path stays in the CLI, and that is why `events` is a parameter.** `SPEC.md` T20
calls `--replay` developer machinery; making the fold take events rather than read them keeps
one fold over two sources instead of two folds. What the app supplies is a live ledger, what
the terminal supplies is either.

**A walk-away under one credit is not a chase**, and this is where that stops being a
rendering detail: `format_advisory` already says "freely replaceable" rather than "walk-away
0", because the bidder's smallest possible raise is `current + step` and refuses at every
price. The app needs the same distinction as a value, or its screen will say chase over a
player the bidder will never bid on.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from fantabot.domain.asta.live import AssignmentEvent


def _event(player: str, *, price: int, buyer: str) -> AssignmentEvent:
    return AssignmentEvent(player_id=player, price=price, buyer_team_id=buyer)


def _request(**over: Any) -> Any:
    from fantabot.application.asta_advisory import AdvisoryRequest

    fields: dict[str, Any] = {
        "our_team_id": "US",
        "season": "2026/27",
        "as_of": date(2026, 9, 20),
        "budget": 500.0,
        "lam": 0.3,
        "tilt_k": 0.25,
        "sentiment": True,
        "sentiment_run": None,
        "num_teams": 10,
        "num_credits": 650,
    }
    fields.update(over)
    return AdvisoryRequest(**fields)


def _patched(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool: list[Any],
    walkaways: dict[str, float],
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fake the two reads and the solve. What is asserted is which request reached them."""
    from fantabot.adapters.persistence import news_sentiment
    from fantabot.application import asta_planner
    from fantabot.application.plan_inputs import PlanInputs
    from fantabot.domain.asta import reservation

    seen: dict[str, Any] = {}

    def fake_read(_session: Any, **kwargs: Any) -> PlanInputs:
        seen["read"] = kwargs
        return PlanInputs(
            pool=pool, value=lambda _p: 1.0, prices={}, teams={}, names=names or {},
            roles={"7": ["Pc"]}, legality={}, sentiment=None,
        )

    class _Result:
        objective = 1897.0

        def __init__(self) -> None:
            self.optimal = _Roster()

    class _Roster:
        objective = 1897.0
        total_cost = 412

        def __iter__(self) -> Any:
            return iter(("1", "2"))

        def __len__(self) -> int:
            return 2

    def fake_rolling(state: Any, _pool: Any, events: Any, **kwargs: Any) -> Any:
        seen["rolling"] = kwargs
        seen["state"] = state
        for event in events:
            yield state, event, _Result(), walkaways

    monkeypatch.setattr(asta_planner, "read_plan_inputs", fake_read)
    monkeypatch.setattr(reservation, "rolling_advisory", fake_rolling)
    monkeypatch.setattr(news_sentiment, "NewsSentimentSource", lambda _s: _Source())
    return seen


class _Source:
    """`SentimentSource`, faked. One method, which is why the protocol is that narrow."""

    def all_latest(self, *, data_run: date | None = None) -> dict[str, Any]:
        return {"1": object()}


class TestTheWorldIsReadTheWayEveryOtherPlanReadsIt:
    def test_the_callable_pool_is_narrowed_to_what_the_listone_can_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """41 of 570 pool players are absent from FantaLab's listone and can never come up.
        This is the advisory an operator bids by hand from, so it must not head its list
        with a player who cannot be called — defect B3, in the surface that shows it."""
        from fantabot.application.asta_advisory import build_advisory

        seen = _patched(monkeypatch, pool=[object()], walkaways={})
        build_advisory(object(), _request(), events=[], bridge={"uuid-1": 7})

        assert seen["read"]["callable_ids"] == frozenset({"7"})

    def test_an_empty_bridge_disables_the_filter_rather_than_emptying_the_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`None`, never `frozenset()`. `read_plan_inputs` reads an empty collection as a
        real, total exclusion — so an unreachable listone would not degrade the advisory,
        it would empty it, and a blank screen reads as a quiet room. `PlanRequest` carries
        the same warning in a comment; this is the assertion behind it.

        Survivor 2 of this slice's battery: `frozenset(...)` without the `or None` passed
        every other test in the file, because they all supply a bridge.
        """
        from fantabot.application.asta_advisory import build_advisory

        seen = _patched(monkeypatch, pool=[object()], walkaways={})
        build_advisory(object(), _request(), events=[], bridge={})

        assert seen["read"]["callable_ids"] is None

    def test_the_corpus_shape_is_the_rooms_own_never_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An 8x500 default against a 10x650 room prices every target against another
        lega's game. With no corpus at all the budget constraint is vacuous and a Classic
        plan bought its 25-man roster for 25 credits of 500."""
        from fantabot.application.asta_advisory import build_advisory

        seen = _patched(monkeypatch, pool=[object()], walkaways={})
        build_advisory(object(), _request(), events=[], bridge={})

        assert (seen["read"]["num_teams"], seen["read"]["num_credits"]) == (10, 650)

    def test_the_calendar_is_the_requests_and_never_read_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`as_of` is a parameter for `domain/asta/sentiment.py`'s reason: a module that
        reads the clock has tests that are a coin flip."""
        from fantabot.application.asta_advisory import build_advisory

        seen = _patched(monkeypatch, pool=[object()], walkaways={})
        build_advisory(object(), _request(), events=[], bridge={})

        assert seen["read"]["as_of"] == date(2026, 9, 20)


class TestTheLedgerIsTranslatedBeforeItIsFolded:
    def test_uuids_become_fantacalcio_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without this the first lot we own puts a uuid into `AstaState.owned` and the
        optimizer raises for an id absent from the pool."""
        from fantabot.application.asta_advisory import build_advisory

        _patched(monkeypatch, pool=[object()], walkaways={})
        advisory = build_advisory(
            object(), _request(), events=[_event("uuid-1", price=40, buyer="THEM")],
            bridge={"uuid-1": 7},
        )

        assert advisory.opponents[0].team_id == "THEM"
        assert advisory.sales == 1 and advisory.dropped_sales == 0

    def test_a_sale_the_listone_cannot_name_is_dropped_and_counted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each is a purchase we never subtracted, so a rival's budget and that player's
        availability are both wrong until it is explained. Counted, not silent."""
        from fantabot.application.asta_advisory import build_advisory

        _patched(monkeypatch, pool=[object()], walkaways={})
        advisory = build_advisory(
            object(), _request(),
            events=[_event("uuid-1", price=40, buyer="THEM"), _event("ghost", price=9, buyer="X")],
            bridge={"uuid-1": 7},
        )

        assert advisory.sales == 1 and advisory.dropped_sales == 1


class TestTheAdvisoryItself:
    def test_no_sales_is_an_answer_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An asta that has not started is the ordinary state at 20:59."""
        from fantabot.application.asta_advisory import build_advisory

        _patched(monkeypatch, pool=[object()], walkaways={})
        advisory = build_advisory(object(), _request(), events=[], bridge={})

        assert advisory.targets == () and advisory.sales == 0

    def test_targets_are_highest_walk_away_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.application.asta_advisory import build_advisory

        _patched(
            monkeypatch, pool=[object()], walkaways={"1": 12.0, "2": 44.0},
            names={"1": "Svilar", "2": "Zaccagni"},
        )
        advisory = build_advisory(
            object(), _request(), events=[_event("uuid-1", price=1, buyer="THEM")],
            bridge={"uuid-1": 7},
        )

        assert [t.nome for t in advisory.targets] == ["Zaccagni", "Svilar"]

    def test_a_walk_away_under_one_credit_is_not_a_chase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`reservations` clamps a negative marginal to zero — it means only that he is
        freely replaceable — and the bidder refuses at every price, because its smallest
        possible raise is `current + step` and that is at least 1. A screen saying "chase,
        walk-away 0" reads as an instruction to do the one thing the system will not do.

        He stays on the list: he is in the target roster and the operator should see him.
        """
        from fantabot.application.asta_advisory import build_advisory

        _patched(
            monkeypatch, pool=[object()], walkaways={"1": 0.0, "2": 44.0},
            names={"1": "Svilar", "2": "Zaccagni"},
        )
        advisory = build_advisory(
            object(), _request(), events=[_event("uuid-1", price=1, buyer="THEM")],
            bridge={"uuid-1": 7},
        )

        freely = next(t for t in advisory.targets if t.nome == "Svilar")
        assert freely.chase is False and freely.walk_away == 0
        assert next(t for t in advisory.targets if t.nome == "Zaccagni").chase is True

    def test_every_rival_carries_what_it_has_left_against_the_rooms_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`remaining` is against the room's declared credits, not ours: the two are the
        same number only in a room where every seat started equal, which is the ordinary
        case and exactly why getting it from the wrong place would never be noticed."""
        from fantabot.application.asta_advisory import build_advisory

        _patched(monkeypatch, pool=[object()], walkaways={})
        advisory = build_advisory(
            object(), _request(num_credits=650), events=[_event("uuid-1", price=40, buyer="THEM")],
            bridge={"uuid-1": 7},
        )

        [rival] = advisory.opponents
        assert (rival.spent, rival.remaining, rival.players) == (40, 610, 1)

    def test_our_own_purchases_are_not_an_opponent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fantabot.application.asta_advisory import build_advisory

        _patched(monkeypatch, pool=[object()], walkaways={})
        advisory = build_advisory(
            object(), _request(), events=[_event("uuid-1", price=40, buyer="US")],
            bridge={"uuid-1": 7},
        )

        assert advisory.opponents == ()

    def test_an_empty_pool_refuses_rather_than_advising_on_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`build_plan`'s own rule. An advisory over no players is a blank screen that
        looks like a quiet room."""
        from fantabot.application.asta_advisory import build_advisory
        from fantabot.application.plan_request import EmptyPool

        _patched(monkeypatch, pool=[], walkaways={})
        with pytest.raises(EmptyPool):
            build_advisory(object(), _request(), events=[], bridge={})
