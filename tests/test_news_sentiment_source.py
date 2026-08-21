"""T9: reading the sentiment time-series back.

The one rule that matters here: rows with confidenza=0 are excluded from every
average. A 0.0 sentiment from silence and a 0.0 from balanced coverage are
different facts, and averaging them together destroys the distinction the schema
was built to preserve.
"""

from pathlib import Path

import pytest

from fantabot.data_sources.news_sentiment import NewsSentimentSource
from fantabot.news.store import append_rows

HEADER_ROW = {
    "stagione": "2026/27",
    "nome": "Zaccagni",
    "squadra": "LAZ",
    "ruolo": "Centrocampista",
    "ruoli_mantra": "W;A",
    "giorni_lookback": "14",
    "modello": "claude-sonnet-5",
    "riassunto": "x",
    "n_fonti": "2",
    "fonti": "https://a;https://b",
    "disponibilita": "1.00",
    "titolarita": "0.90",
    "mercato": "0.00",
    "forma": "0.00",
    "rigorista": "0.80",
    "piazzati": "0.10",
}


def _row(data_run: str, player_id: str = "632", **overrides: str) -> dict[str, str]:
    row = {
        **HEADER_ROW,
        "data_run": data_run,
        "id": player_id,
        "sentiment": "0.50",
        "confidenza": "0.80",
        "ruolo_campo": "",
        "deriva_ruolo": "0.00",
    }
    row.update(overrides)
    return row


def _source(tmp_path: Path, rows: list[dict[str, str]]) -> NewsSentimentSource:
    path = tmp_path / "sentiment.csv"
    append_rows(path, rows)
    return NewsSentimentSource(path)


def test_latest_returns_the_most_recent_row(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [_row("2026-09-02", sentiment="0.10"), _row("2026-10-07", sentiment="0.70")],
    )

    latest = source.latest("632")

    assert latest is not None
    assert latest.data_run == "2026-10-07"
    assert latest.sentiment == 0.70


def test_latest_does_not_depend_on_file_order(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [_row("2026-10-07", sentiment="0.70"), _row("2026-09-02", sentiment="0.10")],
    )

    latest = source.latest("632")
    assert latest is not None
    assert latest.data_run == "2026-10-07"


def test_latest_for_an_unknown_player_is_none(tmp_path: Path) -> None:
    assert _source(tmp_path, [_row("2026-10-07")]).latest("99999") is None


def test_a_missing_file_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    source = NewsSentimentSource(tmp_path / "absent.csv")

    assert source.latest("632") is None
    assert source.trailing("632") is None


def test_trailing_averages_the_window(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            _row("2026-09-16", sentiment="0.20"),
            _row("2026-09-23", sentiment="0.40"),
        ],
    )

    trailing = source.trailing("632", weeks=4)

    assert trailing is not None
    assert trailing.sentiment == pytest.approx(0.30)


def test_a_silent_row_does_not_move_the_mean(tmp_path: Path) -> None:
    # The whole point. A confidenza=0 row means "nobody wrote about him", not
    # "he is neutral"; folding its 0.0 into the average would invent a data point.
    source = _source(
        tmp_path,
        [
            _row("2026-09-16", sentiment="0.20"),
            _row("2026-09-23", sentiment="0.40"),
            _row("2026-09-30", sentiment="0.00", confidenza="0.00"),
        ],
    )

    trailing = source.trailing("632", weeks=4)

    assert trailing is not None
    assert trailing.sentiment == pytest.approx(0.30)
    assert trailing.rows_used == 2


def test_trailing_is_none_when_every_row_is_silent(tmp_path: Path) -> None:
    source = _source(tmp_path, [_row("2026-09-30", sentiment="0.00", confidenza="0.00")])

    assert source.trailing("632") is None


def test_trailing_only_counts_the_requested_window(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [
            _row("2026-08-05", sentiment="1.00"),  # older than 4 rows back
            _row("2026-09-09", sentiment="0.00"),
            _row("2026-09-16", sentiment="0.00"),
            _row("2026-09-23", sentiment="0.00"),
            _row("2026-09-30", sentiment="0.00"),
        ],
    )

    trailing = source.trailing("632", weeks=4)

    assert trailing is not None
    assert trailing.rows_used == 4
    assert trailing.sentiment == 0.0


def test_drift_surfaces_a_stale_tag(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        [_row("2026-10-07", ruolo_campo="T", deriva_ruolo="0.85")],
    )

    drift = source.drift("632")

    assert drift is not None
    assert drift.deriva_ruolo == 0.85
    assert drift.ruolo_campo == "T"
    assert drift.ruoli_mantra == "W;A"


def test_drift_is_none_when_nothing_was_observed(tmp_path: Path) -> None:
    source = _source(tmp_path, [_row("2026-10-07", ruolo_campo="", deriva_ruolo="0.00")])

    assert source.drift("632") is None


def test_players_with_a_stale_tag_can_be_listed(tmp_path: Path) -> None:
    # What a Mantra lineup engine actually wants: everyone whose frozen tag no
    # longer describes them, worst first.
    source = _source(
        tmp_path,
        [
            _row("2026-10-07", player_id="1", ruolo_campo="T", deriva_ruolo="0.85"),
            _row("2026-10-07", player_id="2", ruolo_campo="W", deriva_ruolo="0.00"),
            _row("2026-10-07", player_id="3", ruolo_campo="M", deriva_ruolo="0.60"),
        ],
    )

    assert [d.player_id for d in source.drifted()] == ["1", "3"]
