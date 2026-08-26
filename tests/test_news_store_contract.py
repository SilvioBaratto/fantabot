"""Two contracts a Postgres port breaks silently. Pinned before anything moves.

Neither is covered by the existing suites, and both fail in a way that reports
success:

* ``existing_keys`` returns ``set[tuple[str, str]]`` and ``cli.py:102`` compares
  it against ``(today.isoformat(), p.id)`` — both ``str``. If a repository
  returns ``(date, int)`` instead, the resume filter matches nothing, all 523
  players are re-queried, and the run finishes reporting success while spending
  a full pass for nothing.
* ``deriva_ruolo`` is a **confidence value**, not a flag. ``mantra.drift``
  returns ``0.0`` or the model's ``confidenza``, and ``drifted()`` ranks players
  by it. Stored as a boolean the ranking collapses to arbitrary order and
  nothing looks wrong.

These run against unmodified ``src/`` on purpose: they describe what is true
today, so the port has something to be measured against.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fantabot.news.models import PlayerSentiment
from fantabot.news.pool import PoolPlayer
from fantabot.news.store import build_row, existing_keys

AHANOR = PoolPlayer(
    id="6916", nome="Ahanor", squadra="ATA", ruolo="Difensore", ruoli_mantra="B;DS;E"
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


def _row(**overrides: object) -> dict[str, str]:
    return build_row(
        player=AHANOR,
        sentiment=_sentiment(**overrides),
        data_run=RUN_DAY,
        giorni_lookback=14,
        stagione="2026/27",
        modello="test",
    )


class TestResumeKeyShape:
    """The key that makes killing a half-finished 523-player run free."""

    def test_both_elements_are_strings(self, tmp_path: Path) -> None:
        path = tmp_path / "player_sentiment_2026-27.csv"
        path.write_text("data_run,id\n2026-10-07,6916\n", encoding="utf-8")

        keys = existing_keys(path)

        assert keys == {("2026-10-07", "6916")}
        for data_run, player_id in keys:
            assert isinstance(data_run, str)
            assert isinstance(player_id, str)

    def test_it_matches_what_the_cli_compares_against(self, tmp_path: Path) -> None:
        """cli.py:102 builds (today.isoformat(), p.id). A repository returning
        (date, int) would match nothing and silently re-query everyone."""
        path = tmp_path / "player_sentiment_2026-27.csv"
        path.write_text(f"data_run,id\n{RUN_DAY.isoformat()},{AHANOR.id}\n", encoding="utf-8")

        assert (RUN_DAY.isoformat(), AHANOR.id) in existing_keys(path)

    def test_a_missing_file_is_an_empty_set_not_an_error(self, tmp_path: Path) -> None:
        assert existing_keys(tmp_path / "nope.csv") == set()


class TestDerivaRuoloIsAConfidenceNotAFlag:
    """SPEC called this boolean; the code has always written a float. Ruled on
    2026-08-26 in favour of the code, and SPEC amended to numeric(3,2)."""

    def test_a_stale_tag_records_the_model_s_own_confidence(self) -> None:
        # Observed W, tagged B;DS;E — the observation is not covered by the tag.
        value = _row(ruolo_campo=["W"], confidenza=0.7)["deriva_ruolo"]

        assert value not in {"True", "False", "true", "false"}
        assert float(value) == 0.7
        assert 0.0 < float(value) <= 1.0

    def test_a_tag_that_still_holds_records_zero(self) -> None:
        value = _row(ruolo_campo=["B"], confidenza=0.7)["deriva_ruolo"]

        assert float(value) == 0.0

    def test_silence_is_not_confirmation(self) -> None:
        """An empty observation means the sources said nothing about his
        position, which is a different fact from the tag being right."""
        assert float(_row(ruolo_campo=[], confidenza=0.9)["deriva_ruolo"]) == 0.0

    def test_two_stale_players_are_rankable_against_each_other(self) -> None:
        """The property a boolean column would destroy: drifted() orders by
        this value, and both of these would collapse to True."""
        low = float(_row(ruolo_campo=["W"], confidenza=0.3)["deriva_ruolo"])
        high = float(_row(ruolo_campo=["W"], confidenza=0.9)["deriva_ruolo"])

        assert low < high

    def test_it_is_written_with_two_decimal_places(self) -> None:
        """Which is what numeric(3,2) has to preserve across the port."""
        assert _row(ruolo_campo=["W"], confidenza=0.75)["deriva_ruolo"] == "0.75"
