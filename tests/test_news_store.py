"""T6: the CSV store.

Append-only. History is not reproducible — a past Wednesday's news has moved on —
so a rewrite is a data-loss event and the resume index exists to make a killed
run free to restart.
"""

import csv
from datetime import date
from pathlib import Path

from fantabot.news.models import PlayerSentiment
from fantabot.news.pool import PoolPlayer
from fantabot.news.store import COLUMNS, append_rows, build_row, existing_keys

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


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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

    from fantabot.news.mantra import UnknownRoleCode

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


# --- append_rows ---------------------------------------------------------


def test_the_header_is_written_once_on_creation(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"

    append_rows(path, [_row()])
    append_rows(path, [_row(player=ZACCAGNI)])

    assert path.read_text(encoding="utf-8").count("data_run,") == 1


def test_appending_preserves_prior_rows(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"

    append_rows(path, [_row()])
    append_rows(path, [_row(player=ZACCAGNI)])

    assert [r["id"] for r in _read(path)] == ["6916", "632"]


def test_prose_containing_commas_quotes_and_newlines_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    awkward = 'Out 2-3 settimane, poi "forse" rientra\ncon la Roma.'

    append_rows(path, [_row(riassunto=awkward)])

    assert _read(path)[0]["riassunto"] == awkward


def test_appending_nothing_does_not_create_a_file(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"

    append_rows(path, [])

    assert not path.exists()


# --- existing_keys -------------------------------------------------------


def test_existing_keys_on_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert existing_keys(tmp_path / "absent.csv") == set()


def test_existing_keys_indexes_by_run_and_player(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    append_rows(path, [_row(), _row(player=ZACCAGNI)])

    assert existing_keys(path) == {("2026-10-07", "6916"), ("2026-10-07", "632")}


def test_a_player_from_another_run_day_does_not_block_today(tmp_path: Path) -> None:
    # The whole point of the time-series: the same player gets a row every week.
    path = tmp_path / "s.csv"
    append_rows(path, [_row()])

    keys = existing_keys(path)

    assert ("2026-10-07", "6916") in keys
    assert ("2026-10-14", "6916") not in keys
