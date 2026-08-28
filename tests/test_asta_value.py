"""L2 v1 — the ValueModel interface and the naive point-value. Pure and synchronous.

`NaiveValueModel` is deliberately dumb: it takes a per-player value signal (v1 feeds it
the market quotazione / target price as a proxy for season points) and returns a mean +
a variance. A player the market priced but who has no playing history keeps his mean and
gets a wider band; a player with no signal at all shrinks to the prior with the widest
band. The real Black-Litterman value layer slots in later behind the same Protocol.
"""

from __future__ import annotations

from fantabot.asta_engine.value import NaiveValueModel, PlayerValue, ValueModel


def _model(**overrides: object) -> NaiveValueModel:
    kwargs: dict[str, object] = {
        "signals": {"1": 20.0, "2": 8.0},
        "prior_mean": 3.0,
        "base_variance": 4.0,
        "no_history_variance": 25.0,
        "no_history": frozenset({"2"}),
    }
    kwargs.update(overrides)
    return NaiveValueModel(**kwargs)  # type: ignore[arg-type]


def test_naive_model_satisfies_the_value_protocol() -> None:
    assert isinstance(_model(), ValueModel)


def test_a_player_with_history_gets_his_signal_and_the_base_band() -> None:
    assert _model().value("1") == PlayerValue(mean=20.0, variance=4.0)


def test_a_no_history_player_keeps_his_signal_but_gets_a_wider_band() -> None:
    # The market priced him; we just trust the number less.
    got = _model().value("2")
    assert got.mean == 8.0
    assert got.variance == 25.0


def test_an_unknown_player_shrinks_to_the_prior_with_the_widest_band() -> None:
    assert _model().value("999") == PlayerValue(mean=3.0, variance=25.0)


def test_the_model_is_deterministic() -> None:
    assert _model().value("1") == _model().value("1")
