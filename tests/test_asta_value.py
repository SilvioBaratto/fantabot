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


# --- per-player variance -----------------------------------------------------------
#
# Variance was flat: every player carried the same band, so `lam` was nearly inert and the
# mean-variance objective degenerated to maximizing the mean. `confidenza` is the honest
# per-player uncertainty, and feeding it here is what gives the risk knob something to act
# on.


def test_variance_defaults_to_the_flat_band() -> None:
    """No per-player map supplied — the pre-change behaviour, unchanged."""
    model = _model()

    assert model.value("1").variance == 4.0


def test_a_supplied_variance_overrides_the_base_band() -> None:
    model = _model(variances={"1": 9.0})

    assert model.value("1").variance == 9.0


def test_a_wider_band_does_not_move_the_mean() -> None:
    """Thin coverage means we know less about him, not that he is worth less."""
    flat = _model()
    wide = _model(variances={"1": 16.0})

    assert wide.value("1").mean == flat.value("1").mean
    assert wide.value("1").variance > flat.value("1").variance


def test_no_history_still_dominates_a_supplied_variance() -> None:
    """The market never priced him: that is the wider ignorance, and it wins."""
    model = _model(variances={"2": 5.0})

    assert model.value("2").variance == 25.0


def test_an_unknown_player_is_unaffected_by_the_variance_map() -> None:
    model = _model(variances={"1": 9.0})

    assert model.value("999") == PlayerValue(3.0, 25.0)


def test_no_history_is_a_floor_on_the_band_not_a_replacement() -> None:
    """Never having been priced is the *minimum* ignorance, not the maximum.

    `variance_by_id` can exceed `no_history_variance` — drift widening multiplies past it —
    so short-circuiting here narrowed the band for a drifted no-history player. That is the
    fail-open direction on a rule CLAUDE.md states explicitly: role drift may widen a
    variance. It also gave every no-history player the identical flat band, which is the
    degenerate `lam` case per-player variance exists to remove.
    """
    model = _model(variances={"2": 40.0})

    assert model.value("2").variance == 40.0


def test_a_narrower_supplied_band_still_loses_to_no_history() -> None:
    model = _model(variances={"2": 5.0})

    assert model.value("2").variance == 25.0
