"""T3: Mantra role codes and drift.

fantacalcio.it assigns Mantra roles in late July and never revisits them for the
rest of the season (rules/sistema-mantra.md), so quotazioni_mantra.csv drifts from
reality by design and no file in data/ can correct it. Drift is computed here,
host-side, from what the model observed versus the tag we hold — the model is
never asked whether a tag is stale, because it does not know what tag we hold.
"""

import pytest

from fantabot.news.mantra import MANTRA_CODES, UnknownRoleCode, drift, parse_codes


def test_there_are_twelve_role_codes() -> None:
    # rules/sistema-mantra.md heads its table "Roles (11 codes)" and then lists 12.
    # The table is right; the heading is a typo (fixed in T12).
    assert len(MANTRA_CODES) == 12
    assert sorted(MANTRA_CODES) == [
        "A",
        "B",
        "C",
        "DC",
        "DD",
        "DS",
        "E",
        "M",
        "PC",
        "POR",
        "T",
        "W",
    ]


def test_parse_codes_splits_the_csv_multi_role_form() -> None:
    assert parse_codes("DD;DC") == frozenset({"DD", "DC"})


def test_parse_codes_accepts_the_lowercase_form_used_in_the_rules_doc() -> None:
    assert parse_codes("dd;dc") == frozenset({"DD", "DC"})


def test_parse_codes_tolerates_surrounding_whitespace() -> None:
    assert parse_codes(" DD ; DC ") == frozenset({"DD", "DC"})


def test_parse_codes_on_an_empty_string_is_empty() -> None:
    assert parse_codes("") == frozenset()


def test_parse_codes_rejects_an_unknown_code() -> None:
    # Silently scoring an unrecognised code as "no drift" would hide a join bug
    # behind a column of zeroes.
    with pytest.raises(UnknownRoleCode) as excinfo:
        parse_codes("DD;ZZ")

    assert "ZZ" in str(excinfo.value)


def test_no_drift_when_the_observed_role_matches_the_tag() -> None:
    assert drift(observed=["DD"], tagged="DD;DC", confidenza=0.9) == 0.0


def test_no_drift_when_the_observed_roles_are_a_subset_of_the_tag() -> None:
    assert drift(observed=["DD", "DC"], tagged="DD;DC", confidenza=0.9) == 0.0


def test_no_drift_when_nothing_was_observed() -> None:
    # An empty observation means the sources were silent about his position. It is
    # NOT confirmation that the frozen tag is still correct.
    assert drift(observed=[], tagged="W", confidenza=0.9) == 0.0


def test_drift_is_the_models_own_confidence_when_the_tag_is_stale() -> None:
    # A W-tagged player reported playing as T: the tag is stale, and how sure we
    # are that it is stale is exactly how sure the model was of its reporting.
    assert drift(observed=["T"], tagged="W", confidenza=0.8) == 0.8


def test_drift_when_only_part_of_the_observation_is_outside_the_tag() -> None:
    assert drift(observed=["W", "T"], tagged="W", confidenza=0.6) == 0.6


def test_drift_rejects_an_unknown_observed_code() -> None:
    with pytest.raises(UnknownRoleCode):
        drift(observed=["ZZ"], tagged="W", confidenza=0.5)
