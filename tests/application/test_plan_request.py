"""One door to a plan, and the drift it closes.

`asta optimize` and `GET /asta/plan` each assembled their own inputs and had drifted in
ten of them. The pair that matters: the endpoint passed `sentiment=None` — the sentiment
model's **ablation control**, not "no opinion" — and `tilt_k=1.0`, four times the CLI's
and inert only because there was nothing to tilt. Nothing could say so, because there was
no value that *was* the request.

These tests are about the seam, not the optimizer. `tests/domain/asta/` is where whether a
plan is any good lives; what is checked here is that a request is a value, that the
defaults are the ones the CLI has always used, and that the three failures stay three.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from fantabot.application.plan_request import (
    DEFAULT_NUM_CREDITS,
    DEFAULT_NUM_TEAMS,
    EmptyPool,
    NoSentimentRows,
    PlanRequest,
    build_plan,
    resolve_sentiment,
)
from fantabot.domain.asta.state import RosterRules
from fantabot.domain.shared.values import SentimentRow

RUN = date(2026, 8, 28)


def _row(player_id: str) -> SentimentRow:
    return SentimentRow(
        player_id=player_id, nome=f"p{player_id}", data_run=RUN.isoformat(),
        sentiment=0.5, disponibilita=0.9, titolarita=0.8, mercato=0.5, forma=0.5,
        rigorista=0.0, piazzati=0.0, confidenza=0.7,
        ruolo_campo="Dc", ruoli_mantra="Dc", deriva_ruolo=0.0,
    )


class _FakeSource:
    """Three lines, which is what the narrow `SentimentSource` Protocol buys."""

    def __init__(self, rows: dict[str, SentimentRow]) -> None:
        self.rows = rows
        self.calls: list[date | None] = []

    def all_latest(self, *, data_run: date | None = None) -> dict[str, SentimentRow]:
        self.calls.append(data_run)
        return self.rows


def _request(**over: Any) -> PlanRequest:
    base: dict[str, Any] = {
        "season": "2026/27",
        "listone": "mantra",
        "as_of": RUN,
        "budget": 500.0,
        "rules": RosterRules(),
    }
    return PlanRequest(**{**base, **over})


class TestTheRequestIsAValue:
    def test_two_requests_built_the_same_way_are_equal(self) -> None:
        """1.5 pins the CLI's request against the endpoint's; that needs equality."""
        assert _request() == _request()

    def test_the_order_ids_were_typed_in_does_not_change_the_request(self) -> None:
        """`owned` is a frozenset, so `--owned 1,2` and `--owned 2,1` are one plan."""
        assert _request(owned=frozenset({"1", "2"})) == _request(owned=frozenset({"2", "1"}))

    def test_it_is_frozen(self) -> None:
        with pytest.raises((AttributeError, TypeError)):
            _request().tilt_k = 9.0  # type: ignore[misc]

    def test_it_carries_the_calendar_rather_than_reading_it(self) -> None:
        """The reason `sentiment.py` has no clock: a 7-day confidence half-life makes a
        module that reads `date.today()` a coin flip. `test_asta_clock.py` sweeps
        `application/plan_*.py` by glob so this file is covered the day it exists."""
        assert _request(as_of=date(2020, 1, 1)).as_of == date(2020, 1, 1)

    def test_the_defaults_are_our_league_and_are_defaults(self) -> None:
        """8x500 is the shape the corpus is restricted to, and `docs/fantalab/00 §13` is
        explicit that a league rule written into the code is a bug. It is a default here
        rather than at each call site because five of six call sites passed none at all."""
        assert (_request().num_teams, _request().num_credits) == (
            DEFAULT_NUM_TEAMS,
            DEFAULT_NUM_CREDITS,
        )
        assert _request(num_teams=10, num_credits=1000).num_teams == 10

    def test_callable_ids_defaults_to_none_and_not_to_an_empty_set(self) -> None:
        """`read_plan_inputs` reads an empty collection as a real, total exclusion — right
        for the bidder, and it would empty the planner's pool. 41 of 570 players are absent
        from the listone; Lukaku (2531) is one, at fvm 41."""
        assert _request().callable_ids is None


class TestTheSentimentRule:
    def test_the_ablation_never_queries_the_feed(self) -> None:
        source = _FakeSource({"1": _row("1")})

        assert resolve_sentiment(source, enabled=False, run=None) is None
        assert source.calls == []

    def test_a_pinned_run_reaches_the_source(self) -> None:
        source = _FakeSource({"1": _row("1")})

        resolve_sentiment(source, enabled=True, run=RUN)

        assert source.calls == [RUN]

    def test_no_rows_is_refused_rather_than_silently_skipped(self) -> None:
        """Valuing on zero rows equals `--no-sentiment` numerically and means something
        else. A mistyped date that quietly plans on plain `fvm` is what this prevents."""
        with pytest.raises(NoSentimentRows, match="no rows"):
            resolve_sentiment(_FakeSource({}), enabled=True, run=RUN)

    def test_the_refusal_names_the_run_it_looked_for(self) -> None:
        with pytest.raises(NoSentimentRows, match="2026-08-28"):
            resolve_sentiment(_FakeSource({}), enabled=True, run=RUN)

    def test_and_says_where_it_looked_when_no_run_was_pinned(self) -> None:
        with pytest.raises(NoSentimentRows, match="in the database"):
            resolve_sentiment(_FakeSource({}), enabled=True, run=None)


class TestBuildPlan:
    """The shell. Its reads are faked; what is asserted is which request reached them."""

    @staticmethod
    def _patched(monkeypatch: pytest.MonkeyPatch, *, pool: list[Any]) -> dict[str, Any]:
        """Capture the arguments `build_plan` forwards, and hand back a stub world."""
        from fantabot.adapters.persistence import news_sentiment
        from fantabot.application import asta_planner, plan_request
        from fantabot.application.plan_inputs import PlanInputs
        from fantabot.domain.asta import optimizer

        seen: dict[str, Any] = {}

        def fake_read(_session: Any, **kwargs: Any) -> PlanInputs:
            seen["read"] = kwargs
            return PlanInputs(
                pool=pool, value=lambda _p: 1.0, prices={}, teams={}, names={},
                roles={}, legality={}, sentiment=kwargs["sentiment"],
            )

        def fake_optimize(state: Any, _pool: Any, **kwargs: Any) -> Any:
            seen["state"] = state
            seen["optimize"] = kwargs
            return "planned"

        monkeypatch.setattr(asta_planner, "read_plan_inputs", fake_read)
        monkeypatch.setattr(optimizer, "optimize_roster", fake_optimize)
        monkeypatch.setattr(
            news_sentiment, "NewsSentimentSource", lambda _s: _FakeSource({"1": _row("1")})
        )
        assert plan_request.build_plan  # the module under test, imported
        return seen

    def test_every_field_of_the_request_reaches_the_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ten-input drift, as a test: a field that stops being forwarded fails here."""
        seen = self._patched(monkeypatch, pool=[object()])
        request = _request(
            listone="classic", tilt_k=0.75, num_teams=10, num_credits=1000,
            callable_ids=frozenset({"7"}), sentiment_run=RUN,
        )

        build_plan(object(), request)  # type: ignore[arg-type]

        assert seen["read"]["season"] == request.season
        assert seen["read"]["listone"] == "classic"
        assert seen["read"]["as_of"] == RUN
        assert seen["read"]["tilt_k"] == 0.75
        assert seen["read"]["num_teams"] == 10
        assert seen["read"]["num_credits"] == 1000
        assert seen["read"]["callable_ids"] == frozenset({"7"})
        assert seen["read"]["sentiment"] is not None

    def test_the_ablation_reaches_the_read_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._patched(monkeypatch, pool=[object()])

        build_plan(object(), _request(sentiment=False))  # type: ignore[arg-type]

        assert seen["read"]["sentiment"] is None

    def test_lam_and_fallbacks_reach_the_optimizer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._patched(monkeypatch, pool=[object()])

        build_plan(object(), _request(lam=0.3, n_fallbacks=3))  # type: ignore[arg-type]

        assert seen["optimize"]["lam"] == 0.3
        assert seen["optimize"]["n_fallbacks"] == 3

    def test_owned_reaches_the_state_in_a_stable_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tuple ordered by whatever was typed would make two identical plans differ."""
        seen = self._patched(monkeypatch, pool=[object()])

        build_plan(object(), _request(owned=frozenset({"9", "1", "5"})))  # type: ignore[arg-type]

        assert seen["state"].owned == ("1", "5", "9")

    def test_an_empty_pool_is_refused_rather_than_planned_over(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail closed: no rows for a format and season is a wrong `--format` or an
        un-scraped run, not a plan over nobody."""
        self._patched(monkeypatch, pool=[])

        with pytest.raises(EmptyPool, match="nothing to plan"):
            build_plan(object(), _request())  # type: ignore[arg-type]

    def test_the_empty_pool_message_names_the_format_and_the_season(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patched(monkeypatch, pool=[])

        with pytest.raises(EmptyPool, match="classic players for season 2025/26"):
            build_plan(object(), _request(listone="classic", season="2025/26"))  # type: ignore[arg-type]
