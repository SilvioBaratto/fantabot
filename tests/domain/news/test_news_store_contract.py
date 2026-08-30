"""``deriva_ruolo`` is a confidence value, not a flag.

``mantra.drift`` returns ``0.0`` or the model's own ``confidenza``, and
``drifted()`` ranks players by it. Stored as a boolean the ranking collapses to
arbitrary order and nothing looks wrong. SPEC's Schema called the column
boolean; ruled against on 2026-08-26 in favour of the code, and amended.

This file was written before the port, to describe what was true then. The
other contract it pinned — that ``existing_keys`` returns two strings, because
``cli.py`` compares against ``(today.isoformat(), p.id)`` — now belongs to the
repository, and is asserted against a real ``date`` column in
``tests/integration/test_db.py``, which is where it could actually go wrong.
``build_row`` is unchanged, so everything below still pins live code.
"""

from __future__ import annotations

from datetime import date

from fantabot.domain.news.models import PlayerSentiment
from fantabot.domain.news.pool import PoolPlayer
from fantabot.domain.news.store import build_row

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
