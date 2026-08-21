"""T3: PlayerSentiment, the schema handed to the SDK as the query contract.

Field descriptions in that model are prompt surface, not documentation — the
model reads them — so the tests here guard the shape and the ranges, which are
the parts the pipeline depends on.
"""

import pytest
from pydantic import ValidationError

from fantabot.news.models import PlayerSentiment


def _valid(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sentiment": 0.2,
        "disponibilita": 0.8,
        "titolarita": 0.6,
        "mercato": -0.3,
        "forma": 0.1,
        "rigorista": 0.0,
        "piazzati": 0.0,
        "confidenza": 0.7,
        "riassunto": "Rientrato in gruppo il 12/09, convocabile per la 3a giornata.",
        "fonti": ["https://example.com/a"],
        "ruolo_campo": ["DC"],
    }
    return {**base, **overrides}


def test_a_complete_record_parses() -> None:
    record = PlayerSentiment.model_validate(_valid())

    assert record.sentiment == 0.2
    assert record.ruolo_campo == ["DC"]


def test_signed_scores_reject_values_above_one() -> None:
    with pytest.raises(ValidationError):
        PlayerSentiment.model_validate(_valid(sentiment=1.4))


def test_signed_scores_reject_values_below_minus_one() -> None:
    with pytest.raises(ValidationError):
        PlayerSentiment.model_validate(_valid(mercato=-1.2))


@pytest.mark.parametrize(
    "field", ["disponibilita", "titolarita", "rigorista", "piazzati", "confidenza"]
)
def test_unsigned_scores_reject_negative_values(field: str) -> None:
    # These five are 0..1, not -1..1: a player cannot be less than unavailable.
    with pytest.raises(ValidationError):
        PlayerSentiment.model_validate(_valid(**{field: -0.1}))


def test_an_unknown_field_is_rejected() -> None:
    # extra="forbid": a model inventing a field is a misread prompt, and a silently
    # dropped one would never show up in the CSV to be noticed.
    with pytest.raises(ValidationError):
        PlayerSentiment.model_validate(_valid(fantamedia=6.5))


def test_riassunto_over_the_cap_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PlayerSentiment.model_validate(_valid(riassunto="x" * 401))


def test_a_silent_player_is_representable() -> None:
    # confidenza 0 with no sources and no observed role is the honest answer when
    # coverage says nothing. It must not require inventing a sentiment.
    record = PlayerSentiment.model_validate(
        _valid(
            confidenza=0.0,
            fonti=[],
            ruolo_campo=[],
            riassunto="Nessuna notizia rilevante nel periodo.",
        )
    )

    assert record.confidenza == 0.0
    assert record.fonti == []
    assert record.ruolo_campo == []


def test_the_schema_names_every_field_the_csv_writes() -> None:
    assert set(PlayerSentiment.model_fields) == {
        "sentiment",
        "disponibilita",
        "titolarita",
        "mercato",
        "forma",
        "rigorista",
        "piazzati",
        "confidenza",
        "riassunto",
        "fonti",
        "ruolo_campo",
    }


def test_every_field_carries_a_description_for_the_prompt() -> None:
    # The json_schema is the only instruction the model gets about field meaning.
    missing = [name for name, f in PlayerSentiment.model_fields.items() if not f.description]

    assert missing == []
