"""`decode_claims` — read a league token's payload without trusting it.

Every token here is synthesized by `_tokens.make_token`. The issuer's signing
key is not ours, so nothing in this module verifies a signature; what it pins is
that malformed input becomes one named error and that the error never quotes the
input back.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import _tokens
import pytest

from fantabot.tokens.claims import TokenClaims, decode_claims
from fantabot.tokens.errors import TokenUnreadable

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def test_the_documented_payload_decodes_to_typed_claims() -> None:
    claims = decode_claims(
        _tokens.make_token(l_id=_tokens.LEGA_MANTRA, t_id=_tokens.TEAM_MANTRA)
    )

    assert claims == TokenClaims(
        league_id=4103937,
        team_id=10000003,
        user_id=20000003,
        issued_at=datetime.fromtimestamp(_tokens.IAT, tz=UTC),
        expires_at=datetime.fromtimestamp(_tokens.EXP, tz=UTC),
    )


def test_l_id_arrives_as_a_string_and_comes_back_an_int() -> None:
    """The real payload carries `"l_id": "4103937"`, quoted — see docs/leghe-api.md.

    Coercing here means every caller compares ints, and the `l_id` gate in
    `capture.py` is a comparison it cannot get subtly wrong.
    """
    claims = decode_claims(_tokens.make_token(l_id="4103937"))

    assert claims.league_id == 4103937
    assert isinstance(claims.league_id, int)


@pytest.mark.parametrize("padding_case", range(1, 12))
def test_payloads_of_every_base64_padding_length_decode(padding_case: int) -> None:
    """Base64 segments are written unpadded, so the decoder must re-pad.

    Varying a filler claim's length walks the payload through byte counts
    congruent to 0, 2 and 3 mod 4 — the three cases `b64decode` distinguishes.
    """
    token = _tokens.make_token(l_id=1, filler="x" * padding_case)

    assert decode_claims(token).league_id == 1


def test_a_token_with_no_team_or_user_yields_none_for_both() -> None:
    claims = decode_claims(_tokens.make_token(l_id=1, t_id=None, user_id=None))

    assert claims.team_id is None
    assert claims.user_id is None


# --- expiry ---------------------------------------------------------------


def _claims_expiring_at(when: datetime) -> TokenClaims:
    return decode_claims(_tokens.make_token(l_id=1, exp=int(when.timestamp())))


def test_a_token_thirty_seconds_from_expiry_is_already_expired() -> None:
    """The default one-minute skew: a call starting now would race the clock."""
    claims = _claims_expiring_at(NOW + timedelta(seconds=30))

    assert claims.is_expired(NOW) is True


def test_a_token_ninety_seconds_from_expiry_is_not() -> None:
    claims = _claims_expiring_at(NOW + timedelta(seconds=90))

    assert claims.is_expired(NOW) is False


def test_a_long_lived_token_is_not_expired_and_reports_its_remaining_life() -> None:
    claims = decode_claims(_tokens.make_token(l_id=1))

    assert claims.is_expired(NOW) is False
    assert claims.expires_in(NOW) > timedelta(days=350)


def test_a_token_that_expired_yesterday_is_expired() -> None:
    claims = _claims_expiring_at(NOW - timedelta(days=1))

    assert claims.is_expired(NOW) is True


# --- malformed input ------------------------------------------------------


def _payload_segment(payload: object) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


MALFORMED = {
    "missing exp": _tokens.raw_token(_payload_segment({"l_id": "1", "iat": 1})),
    "missing l_id": _tokens.raw_token(_payload_segment({"iat": 1, "exp": 2})),
    "non-json payload": _tokens.raw_token("bm90LWpzb24"),
    "two segments": "header.payload",
    "empty string": "",
    "l_id not numeric": _tokens.raw_token(
        _payload_segment({"l_id": "nope", "iat": 1, "exp": 2})
    ),
    "payload is a list": _tokens.raw_token(_payload_segment([1, 2, 3])),
    "absurd exp": _tokens.raw_token(
        _payload_segment({"l_id": "1", "iat": 1, "exp": 10**20})
    ),
}


@pytest.mark.parametrize("case", sorted(MALFORMED), ids=sorted(MALFORMED))
def test_every_malformed_token_raises_token_unreadable(case: str) -> None:
    with pytest.raises(TokenUnreadable):
        decode_claims(MALFORMED[case])


def test_an_absurd_exp_does_not_escape_as_an_overflow_error() -> None:
    """`datetime.fromtimestamp(10**20, tz=UTC)` raises `OverflowError`, which is
    NOT a `ValueError` subclass — so it escaped SPEC's original except tuple as
    a raw traceback, breaking the function's own documented promise."""
    with pytest.raises(TokenUnreadable):
        decode_claims(MALFORMED["absurd exp"])


@pytest.mark.parametrize("case", sorted(MALFORMED), ids=sorted(MALFORMED))
def test_no_error_message_quotes_the_token_back(case: str) -> None:
    """A truncated or wrong-format credential in a traceback is still a credential."""
    token = MALFORMED[case]
    try:
        decode_claims(token)
    except TokenUnreadable as exc:
        message = str(exc)
    else:  # pragma: no cover - every case above must raise
        raise AssertionError(f"{case} did not raise")

    leaked = [token[i : i + 8] for i in range(len(token) - 7) if token[i : i + 8] in message]
    assert leaked == [], f"{case}: the message quotes {leaked} back from the token"


def test_pyjwts_own_exception_does_not_chain_into_the_traceback() -> None:
    """`raise ... from None`: PyJWT's message can name the input it choked on."""
    try:
        decode_claims("header.payload")
    except TokenUnreadable as exc:
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
    else:  # pragma: no cover
        raise AssertionError("did not raise")


def test_the_error_names_the_exception_class_so_the_cause_is_still_diagnosable() -> None:
    """Redacting the input must not make the failure unreadable."""
    try:
        decode_claims("header.payload")
    except TokenUnreadable as exc:
        assert "not a readable league token" in str(exc)
        assert "Error" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("did not raise")
