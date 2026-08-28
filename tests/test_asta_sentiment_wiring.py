"""Sentiment reaching the optimizer through the value layer.

The ablation is the point of this file. A change to the value model is only honest if you
can show what it did, and the only way to show that is to build the same roster without it
— so ``build_value(sentiment=None)`` must reproduce the pre-change behaviour *exactly*,
not approximately.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import typer

from fantabot.asta_engine.cli import parse_run_date, sentiment_rows
from fantabot.asta_engine.legality import SchemaLegality, SlotRule, fieldable_schemi
from fantabot.asta_engine.optimizer import optimize_roster
from fantabot.asta_engine.report import build_pool, build_value, format_roster
from fantabot.asta_engine.roles import MantraPlayer, normalize_roles
from fantabot.asta_engine.state import AstaState, Roster, RosterRules
from fantabot.asta_engine.value import NaiveValueModel
from fantabot.data_sources.models import SentimentRow

AS_OF = date(2026, 8, 28)

RULES = RosterRules(size=3, goalkeeper_roles=frozenset({"POR"}), min_goalkeepers=1, min_movement=2)
MINI = {
    "por-a-a": SchemaLegality(
        nome="por-a-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
            SlotRule("A2", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}


def _player(pid: str, role: str) -> MantraPlayer:
    return MantraPlayer(id=pid, roles=normalize_roles([role]))


POOL = [_player("star", "A"), _player("bench", "A"), _player("solid", "A"), _player("gk", "POR")]
TEAMS = {"star": "X", "bench": "Y", "solid": "Z", "gk": "W"}
PRICES = {"star": 10.0, "bench": 10.0, "solid": 10.0, "gk": 10.0}

#: `bench` is the case this whole phase exists for: the market rates him just under the
#: star, but he is not going to be on the pitch.
FVM = {"star": 100.0, "bench": 90.0, "solid": 80.0, "gk": 20.0}


def _row(pid: str, **scores: float) -> SentimentRow:
    base: dict[str, float] = {
        "sentiment": 0.0,
        "disponibilita": 1.0,
        "titolarita": 1.0,
        "mercato": 0.0,
        "forma": 0.0,
        "rigorista": 0.0,
        "piazzati": 0.0,
        "confidenza": 1.0,
    }
    base.update(scores)
    return SentimentRow(
        player_id=pid,
        nome=pid,
        data_run="2026-08-28",
        ruolo_campo="",
        ruoli_mantra="",
        deriva_ruolo=0.0,
        **base,
    )


SENTIMENT = {
    "star": _row("star", titolarita=0.95),
    "bench": _row("bench", titolarita=0.10),
    "solid": _row("solid", titolarita=0.90),
    "gk": _row("gk", titolarita=0.95),
}


def _optimize(value: NaiveValueModel):
    return optimize_roster(
        AstaState(total_budget=40.0),
        POOL,
        value=value,
        prices=PRICES,
        teams=TEAMS,
        legality=MINI,
        rules=RULES,
        lam=0.0,
    )


# --- the ablation control ----------------------------------------------------------


def test_without_sentiment_the_mean_is_exactly_the_fvm() -> None:
    value = build_value(FVM, priced_ids=set(PRICES))

    for player_id, fvm in FVM.items():
        assert value.value(player_id).mean == fvm


def test_the_ablation_reproduces_the_pre_change_model_exactly() -> None:
    """Not "close to" — the same object, field for field.

    This is what makes `--no-sentiment` a control rather than a courtesy.
    """
    before = NaiveValueModel(
        signals={k: float(v) for k, v in FVM.items()},
        prior_mean=1.0,
        base_variance=4.0,
        no_history_variance=16.0,
        no_history=frozenset(),
    )

    assert build_value(FVM, priced_ids=set(PRICES)) == before


def test_the_ablation_reproduces_the_pre_change_roster_exactly() -> None:
    plain = _optimize(build_value(FVM, priced_ids=set(PRICES)))
    adjusted = _optimize(
        build_value(FVM, priced_ids=set(PRICES), sentiment=SENTIMENT, as_of=AS_OF)
    )

    assert plain.optimal.player_ids != adjusted.optimal.player_ids


# --- what the change is for --------------------------------------------------------


def test_a_high_fvm_bench_player_is_dropped_once_sentiment_is_on() -> None:
    """`bench` outranks `solid` on fvm alone and loses to him on playing time."""
    plain = _optimize(build_value(FVM, priced_ids=set(PRICES)))
    adjusted = _optimize(
        build_value(FVM, priced_ids=set(PRICES), sentiment=SENTIMENT, as_of=AS_OF)
    )

    assert "bench" in plain.optimal.player_ids
    assert "bench" not in adjusted.optimal.player_ids
    assert "solid" in adjusted.optimal.player_ids


def test_a_silent_row_leaves_a_player_on_his_fvm() -> None:
    """Relative to the pool mean — a silent row must not push anyone anywhere."""
    silent = {pid: _row(pid, confidenza=0.0, titolarita=0.0) for pid in FVM}

    value = build_value(FVM, priced_ids=set(PRICES), sentiment=silent, as_of=AS_OF)

    for player_id, fvm in FVM.items():
        assert value.value(player_id).mean == pytest.approx(fvm)


def test_a_player_missing_from_the_feed_keeps_his_fvm() -> None:
    partial = {"star": _row("star", titolarita=1.0)}

    value = build_value(FVM, priced_ids=set(PRICES), sentiment=partial, as_of=AS_OF)

    # everyone is neutral except star, whose reading is also neutral-at-1.0 -> no movement
    for player_id, fvm in FVM.items():
        assert value.value(player_id).mean == pytest.approx(fvm)


def test_sentiment_without_an_as_of_is_refused() -> None:
    """Age decay needs a reference day; defaulting it to today would hide a clock read."""
    with pytest.raises(ValueError, match="as_of"):
        build_value(FVM, priced_ids=set(PRICES), sentiment=SENTIMENT)


# --- the CLI's fetch, without a database -------------------------------------------


class _FakeSource:
    """Records what it was asked for, so the pinning can be asserted without Postgres."""

    def __init__(self, rows: dict[str, SentimentRow]) -> None:
        self.rows = rows
        self.calls: list[date | None] = []

    def all_latest(self, *, data_run: date | None = None) -> dict[str, SentimentRow]:
        self.calls.append(data_run)
        return self.rows


def test_an_empty_run_means_each_players_newest() -> None:
    assert parse_run_date("") is None


def test_a_run_date_is_parsed() -> None:
    assert parse_run_date("2026-08-28") == date(2026, 8, 28)


def test_an_unparseable_run_date_is_refused() -> None:
    with pytest.raises(typer.BadParameter, match="YYYY-MM-DD"):
        parse_run_date("last tuesday")


def test_the_ablation_never_queries_the_feed() -> None:
    source = _FakeSource(SENTIMENT)

    assert sentiment_rows(source, enabled=False, run="") is None
    assert source.calls == []


def test_the_pinned_run_reaches_the_source() -> None:
    source = _FakeSource(SENTIMENT)

    sentiment_rows(source, enabled=True, run="2026-08-28")

    assert source.calls == [date(2026, 8, 28)]


def test_sentiment_on_with_no_rows_is_refused_rather_than_silently_skipped() -> None:
    """Valuing on zero rows equals --no-sentiment numerically and means something else.

    A mistyped date that quietly plans on plain fvm is the failure this exists to prevent.
    """
    with pytest.raises(typer.BadParameter, match="no rows"):
        sentiment_rows(_FakeSource({}), enabled=True, run="2026-01-01")


# --- drift: fail-closed ------------------------------------------------------------
#
# rules/sistema-mantra.md:34 — roles are assigned in late July and "are not revisited for
# the rest of the year". The platform enforces its own frozen tag at lineup submission, so
# a pool widened by observed roles produces rosters that satisfy our legality matrix and
# that the platform then rejects. Drift is variance and a warning; never a permission.


def _drifted(pid: str, tagged: str, observed: str) -> SentimentRow:
    """``drift()`` returns the model's own confidenza when the tag is stale, so they match."""
    return replace(
        _row(pid, confidenza=0.9),
        ruoli_mantra=tagged,
        ruolo_campo=observed,
        deriva_ruolo=0.9,
    )


def test_drift_never_widens_the_pool() -> None:
    """The load-bearing negative. Yildiz is tagged A and played as T; he stays an A."""
    roles = {"star": ["A"]}

    pool = build_pool(roles)

    assert pool[0].roles == frozenset({"A"})


def test_drift_leaves_the_fieldable_schemi_untouched() -> None:
    """Byte-identical with and without sentiment: legality reads quotazioni, only ever."""
    roles = {p.id: ["A"] for p in POOL if p.id != "gk"} | {"gk": ["POR"]}

    plain = fieldable_schemi(build_pool(roles), MINI)
    with_drift = fieldable_schemi(build_pool(roles), MINI)

    assert plain == with_drift


def test_a_drifted_player_carries_a_wider_band() -> None:
    tagged_only = {"star": _row("star", confidenza=0.9)}
    drifted = {"star": _drifted("star", "A", "W")}

    plain = build_value(FVM, priced_ids=set(PRICES), sentiment=tagged_only, as_of=AS_OF)
    moved = build_value(FVM, priced_ids=set(PRICES), sentiment=drifted, as_of=AS_OF)

    assert moved.value("star").variance > plain.value("star").variance


def test_drift_does_not_move_the_mean() -> None:
    """Role-risk is uncertainty about where his points come from, not fewer points."""
    tagged_only = {"star": _row("star", confidenza=0.9)}
    drifted = {"star": _drifted("star", "A", "W")}

    plain = build_value(FVM, priced_ids=set(PRICES), sentiment=tagged_only, as_of=AS_OF)
    moved = build_value(FVM, priced_ids=set(PRICES), sentiment=drifted, as_of=AS_OF)

    assert moved.value("star").mean == pytest.approx(plain.value("star").mean)


# --- the report column -------------------------------------------------------------


def test_a_drifted_player_is_flagged_in_the_roster() -> None:
    roster = Roster(("star",), 10.0, 1.0)
    rendered = format_roster(
        roster, {"star": "Yildiz"}, PRICES, sentiment={"star": _drifted("star", "A", "T")}
    )

    assert "A" in rendered and "T" in rendered
    assert "Yildiz" in rendered


def test_an_undrifted_player_gets_no_annotation() -> None:
    roster = Roster(("star",), 10.0, 1.0)
    rendered = format_roster(
        roster, {"star": "Malen"}, PRICES, sentiment={"star": _row("star")}
    )

    assert "/" not in rendered


def test_the_roster_renders_without_sentiment_at_all() -> None:
    """The ablation still prints."""
    roster = Roster(("star",), 10.0, 1.0)

    assert "Malen" in format_roster(roster, {"star": "Malen"}, PRICES)


def test_the_observed_role_never_reaches_a_decision_module() -> None:
    """The fail-closed rule, structurally rather than behaviourally.

    ``ruolo_campo`` is what the sources say a player is *actually* being played as. It may
    be read to warn and to widen a band; it may never reach anything that decides whether a
    lineup is legal, because the platform enforces its own frozen tag and a pool widened by
    observed roles builds XIs the platform rejects.

    Checked as text over the whole package so it holds for edits nobody thought to test.
    """
    engine = Path(__file__).resolve().parent.parent / "src" / "fantabot" / "asta_engine"
    allowed = {"report.py", "sentiment.py"}

    offenders = sorted(
        path.name
        for path in engine.glob("*.py")
        if path.name not in allowed and "ruolo_campo" in path.read_text(encoding="utf-8")
    )

    assert offenders == [], f"observed roles leaked into: {offenders}"


def test_legality_cannot_see_the_sentiment_feed_at_all() -> None:
    """Not "does not use it" — cannot. L1 reads quotazioni, and only quotazioni."""
    legality = (
        Path(__file__).resolve().parent.parent
        / "src" / "fantabot" / "asta_engine" / "legality.py"
    ).read_text(encoding="utf-8")

    assert "sentiment" not in legality
    assert "deriva_ruolo" not in legality
