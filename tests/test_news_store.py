"""build_row: flattening one validated record into the stored columns.

Pure, and unchanged by the move to Postgres — which is the point. The seven
tests that pinned CSV mechanics (header written once, comma and newline
escaping, empty batch creates no file, the resume index) moved to
tests/integration/test_sentiment_storage.py, where the same contracts are
asserted against the table that replaced them.
"""

from datetime import date

from fantabot.domain.news.models import PlayerSentiment
from fantabot.domain.news.pool import PoolPlayer
from fantabot.domain.news.store import COLUMNS, build_row

AHANOR = PoolPlayer(
    id="6916", nome="Ahanor", squadra="ATA", ruolo="Difensore", ruoli_mantra="B;DS;E"
)
ZACCAGNI = PoolPlayer(
    id="632", nome="Zaccagni", squadra="LAZ", ruolo="Centrocampista", ruoli_mantra="W;A"
)
RUN_DAY = date(2026, 10, 7)


def _sentiment(**overrides: object) -> PlayerSentiment:
    base: dict[str, object] = {
        "sentiment": -0.4,
        "disponibilita": 0.2,
        "titolarita": 0.3,
        "mercato": -0.6,
        "forma": 0.0,
        "rigorista": 0.0,
        "piazzati": 0.0,
        "confidenza": 0.7,
        "riassunto": "Infortunio muscolare il 05/10.",
        "fonti": ["https://a", "https://b"],
        "ruolo_campo": [],
    }
    return PlayerSentiment.model_validate({**base, **overrides})


def _row(player: PoolPlayer = AHANOR, **overrides: object) -> dict[str, str]:
    return build_row(
        player=player,
        sentiment=_sentiment(**overrides),
        data_run=RUN_DAY,
        giorni_lookback=14,
        stagione="2026/27",
        modello="claude-sonnet-5",
    )


# --- build_row -----------------------------------------------------------


def test_the_row_carries_every_column() -> None:
    assert set(_row()) == set(COLUMNS)


def test_scores_are_written_with_a_decimal_point() -> None:
    # The scraped CSVs use Italian comma-decimals; data/README.md lists that as a
    # gotcha to work around, not a convention to propagate.
    row = _row()

    assert row["sentiment"] == "-0.40"
    assert row["forma"] == "0.00"


def test_sources_are_joined_and_counted() -> None:
    row = _row()

    assert row["fonti"] == "https://a;https://b"
    assert row["n_fonti"] == "2"


def test_a_silent_player_writes_an_empty_sources_cell() -> None:
    row = _row(fonti=[], confidenza=0.0, riassunto="Nessuna notizia rilevante nel periodo.")

    assert row["fonti"] == ""
    assert row["n_fonti"] == "0"
    assert row["confidenza"] == "0.00"


def test_no_drift_when_the_observed_role_is_covered_by_the_tag() -> None:
    row = _row(player=AHANOR, ruolo_campo=["DS"])

    assert row["ruolo_campo"] == "DS"
    assert row["deriva_ruolo"] == "0.00"


def test_drift_is_recorded_when_the_frozen_tag_is_stale() -> None:
    # Tagged W;A in late July, reported playing as a trequartista. T is in neither
    # slot, so the tag no longer describes him.
    row = _row(player=ZACCAGNI, ruolo_campo=["T"], confidenza=0.8)

    assert row["deriva_ruolo"] == "0.80"


def test_the_observed_role_is_stored_normalized() -> None:
    # Live runs return the rules-doc casing the prompt's legend uses ("B;Ds;E",
    # "Por"), while ruoli_mantra is uppercase. Drift is computed on parsed sets so
    # it was already right, but the stored cell has to be comparable to the tag
    # beside it and greppable across the file.
    row = _row(player=AHANOR, ruolo_campo=["B", "Ds", "E"])

    assert row["ruolo_campo"] == "B;DS;E"


def test_the_observed_role_is_stored_in_a_canonical_order() -> None:
    # "E;Dd" and "Dd;E" are the same observation; the file should spell it one way.
    assert _row(ruolo_campo=["E", "DS"])["ruolo_campo"] == "DS;E"


def test_an_unrecognised_observed_role_is_not_written_as_if_it_were_fine() -> None:
    import pytest as _pytest

    from fantabot.domain.news.mantra import UnknownRoleCode

    with _pytest.raises(UnknownRoleCode):
        _row(ruolo_campo=["ZZ"])


def test_an_unobserved_role_is_not_recorded_as_agreement() -> None:
    row = _row(player=ZACCAGNI, ruolo_campo=[], confidenza=0.8)

    assert row["ruolo_campo"] == ""
    assert row["deriva_ruolo"] == "0.00"


def test_the_row_carries_the_run_metadata() -> None:
    row = _row()

    assert row["data_run"] == "2026-10-07"
    assert row["giorni_lookback"] == "14"
    assert row["stagione"] == "2026/27"
    assert row["modello"] == "claude-sonnet-5"
