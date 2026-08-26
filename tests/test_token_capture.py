"""`parse_storage_state` — a browser session blob into league-checked tokens.

Every blob here is built by `_tokens.storage_state`, hand-written from the shape
recorded in `docs/leghe-api.md`. **Never produced by redacting a real one**: a
redaction bug commits a live 365-day credential.

The gate that matters most is the `l_id` check. `docs/leghe-api.md` calls it the
only thing standing between a mislabelled token and acting in the wrong lega,
and it refuses the *whole* capture rather than skipping the bad entry.
"""

from __future__ import annotations

import json
from typing import Any

import _tokens
import pytest

from fantabot.tokens.capture import ORIGIN, CapturedToken, parse_storage_state
from fantabot.tokens.errors import (
    LeagueMismatch,
    NoLeaguesFound,
    TokenError,
    TokenUnreadable,
)


def _blob_of(state: dict[str, Any]) -> dict[str, Any]:
    """The parsed LEAGUES2024_LOCAL out of a storage_state, for mutation."""
    entries = state["origins"][0]["localStorage"]
    raw = next(e["value"] for e in entries if e["name"] == "LEAGUES2024_LOCAL")
    return json.loads(raw)


def _with_blob(state: dict[str, Any], blob: Any) -> dict[str, Any]:
    entries = state["origins"][0]["localStorage"]
    for entry in entries:
        if entry["name"] == "LEAGUES2024_LOCAL":
            entry["value"] = blob if isinstance(blob, str) else json.dumps(blob)
    return state


# --- the happy path -------------------------------------------------------


def test_both_leghe_are_captured_in_order() -> None:
    captured = parse_storage_state(_tokens.storage_state(), origin=ORIGIN)

    assert [c.league_id for c in captured] == [_tokens.LEGA_CLASSIC, _tokens.LEGA_MANTRA]
    assert [c.claims.team_id for c in captured] == [_tokens.TEAM_CLASSIC, _tokens.TEAM_MANTRA]
    assert [c.league_name for c in captured] == ["Legamiallerotaie", "Legamiallerotaie2"]


def test_the_captured_token_carries_the_string_it_was_given() -> None:
    state = _tokens.storage_state()
    expected = _blob_of(state)[f"current-user-{_tokens.USER_ID}"]["leagues"][0]["token"]

    assert parse_storage_state(state, origin=ORIGIN)[0].token == expected


def test_a_single_lega_account_captures_one() -> None:
    one = [
        {
            "id": _tokens.LEGA_CLASSIC,
            "name": "Solo",
            "token": _tokens.make_token(l_id=_tokens.LEGA_CLASSIC),
        }
    ]

    assert len(parse_storage_state(_tokens.storage_state(leagues=one), origin=ORIGIN)) == 1


# --- the l_id gate: SC 10 -------------------------------------------------


def test_a_crossed_l_id_refuses_the_entire_capture() -> None:
    """SC 10. The other entries are not evidence once the blob's shape is wrong."""
    crossed = [
        {
            "id": _tokens.LEGA_CLASSIC,
            "name": "Legamiallerotaie",
            "token": _tokens.make_token(l_id=_tokens.LEGA_MANTRA),  # wrong lega
        },
        {
            "id": _tokens.LEGA_MANTRA,
            "name": "Legamiallerotaie2",
            "token": _tokens.make_token(l_id=_tokens.LEGA_MANTRA),  # this one is fine
        },
    ]

    with pytest.raises(LeagueMismatch) as caught:
        parse_storage_state(_tokens.storage_state(leagues=crossed), origin=ORIGIN)

    message = str(caught.value)
    assert str(_tokens.LEGA_CLASSIC) in message
    assert str(_tokens.LEGA_MANTRA) in message


def test_the_mismatch_message_contains_no_token() -> None:
    token = _tokens.make_token(l_id=_tokens.LEGA_MANTRA)
    crossed = [{"id": _tokens.LEGA_CLASSIC, "name": "x", "token": token}]

    with pytest.raises(LeagueMismatch) as caught:
        parse_storage_state(_tokens.storage_state(leagues=crossed), origin=ORIGIN)

    message = str(caught.value)
    leaked = [token[i : i + 8] for i in range(len(token) - 7) if token[i : i + 8] in message]
    assert leaked == []


# --- every malformed shape ------------------------------------------------


def _cases() -> dict[str, dict[str, Any]]:
    no_origin = _tokens.storage_state()
    no_origin["origins"] = []

    wrong_origin = _tokens.storage_state(origin="https://www.fantacalcio.it")

    no_key = _tokens.storage_state(include_blob=False)

    not_json = _with_blob(_tokens.storage_state(), "{not json")

    localstorage_dict = _tokens.storage_state()
    localstorage_dict["origins"][0]["localStorage"] = {"LEAGUES2024_LOCAL": "{}"}

    blob = _blob_of(_tokens.storage_state())
    del blob["current-user"]
    no_pointer = _with_blob(_tokens.storage_state(), blob)

    blob = _blob_of(_tokens.storage_state())
    del blob[f"current-user-{_tokens.USER_ID}"]
    no_user = _with_blob(_tokens.storage_state(), blob)

    blob = _blob_of(_tokens.storage_state())
    del blob[f"current-user-{_tokens.USER_ID}"]["leagues"]
    no_leagues = _with_blob(_tokens.storage_state(), blob)

    return {
        "no origins": no_origin,
        "wrong origin": wrong_origin,
        "no LEAGUES2024_LOCAL": no_key,
        "value is not json": not_json,
        "localStorage is a dict": localstorage_dict,
        "no current-user pointer": no_pointer,
        "no current-user-{uid}": no_user,
        "leagues key absent": no_leagues,
        "leagues empty": _tokens.storage_state(leagues=[]),
        "leagues not a list": _with_blob(
            _tokens.storage_state(),
            {
                "current-user": _tokens.USER_ID,
                f"current-user-{_tokens.USER_ID}": {"leagues": "nope"},
            },
        ),
        "entry has no token": _tokens.storage_state(leagues=[{"id": 1, "name": "x"}]),
        "entry has no id": _tokens.storage_state(
            leagues=[{"name": "x", "token": _tokens.make_token(l_id=1)}]
        ),
        "token is undecodable": _tokens.storage_state(
            leagues=[{"id": 1, "name": "x", "token": "not.a.token"}]
        ),
        "no blob at all": {"cookies": [], "origins": [{"origin": ORIGIN, "localStorage": []}]},
    }


CASES = _cases()


@pytest.mark.parametrize("case", sorted(CASES), ids=sorted(CASES))
def test_every_malformed_shape_raises_a_token_error(case: str) -> None:
    """Never a bare KeyError, TypeError or JSONDecodeError escaping the walk."""
    with pytest.raises(TokenError):
        parse_storage_state(CASES[case], origin=ORIGIN)


def test_an_absent_blob_says_to_finish_signing_in() -> None:
    with pytest.raises(NoLeaguesFound) as caught:
        parse_storage_state(CASES["no LEAGUES2024_LOCAL"], origin=ORIGIN)

    assert "signing in" in str(caught.value)


def test_an_undecodable_token_surfaces_as_token_unreadable() -> None:
    with pytest.raises(TokenUnreadable):
        parse_storage_state(CASES["token is undecodable"], origin=ORIGIN)


# --- the warn-only cross-checks -------------------------------------------


def test_a_foreign_user_id_warns_but_still_captures() -> None:
    """A shared browser profile, or the wrong account. Worth saying; not a refusal.

    SPEC's Always list names exactly one assertion, the `l_id` one. A refusal
    SPEC did not ask for can block a legitimate capture with no override.
    """
    leagues = [
        {
            "id": _tokens.LEGA_CLASSIC,
            "name": "x",
            "token": _tokens.make_token(l_id=_tokens.LEGA_CLASSIC, user_id=999999),
        }
    ]

    with pytest.warns(UserWarning, match="user_id"):
        captured = parse_storage_state(_tokens.storage_state(leagues=leagues), origin=ORIGIN)

    assert len(captured) == 1


def test_the_happy_path_warns_about_nothing() -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        parse_storage_state(_tokens.storage_state(), origin=ORIGIN)


# --- the repr SPEC's secrecy list misses ----------------------------------


def test_the_captured_token_repr_does_not_leak_the_jwt() -> None:
    """A frozen dataclass's generated `__repr__` prints every field.

    SPEC's leak test names only `LeagueToken.__repr__` and misses this one
    entirely — and this is the object that holds the *plaintext*.
    """
    captured: CapturedToken = parse_storage_state(_tokens.storage_state(), origin=ORIGIN)[0]
    rendered = repr(captured)

    token = captured.token
    leaked = [token[i : i + 8] for i in range(len(token) - 7) if token[i : i + 8] in rendered]
    assert leaked == [], f"CapturedToken.__repr__ exposes {leaked}"
    assert str(captured.league_id) in rendered


def test_a_list_of_captured_tokens_is_safe_to_print() -> None:
    """pytest prints the whole list on any failing assertion about it."""
    captured = parse_storage_state(_tokens.storage_state(), origin=ORIGIN)
    rendered = repr(captured)

    for one in captured:
        assert one.token[:12] not in rendered
