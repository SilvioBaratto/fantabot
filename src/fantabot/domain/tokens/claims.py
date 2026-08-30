"""Read a league token's payload. Pure: values in, values out.

The issuer's signing key is not ours and never will be. We read this token's
claims to learn which lega it is for and when it dies; the site is the only thing
that can decide whether it is genuine, and it does that on every call. A `401
ATH001` is that decision arriving.

**PyJWT mutates ``_DECODE_OPTIONS`` in place.** Each ``decode`` adds six more
``verify_*: False`` keys (nbf, iat, aud, iss, sub, jti). It is idempotent, so a
shared module constant is safe — documented here rather than defended against,
because the obvious defence is worse: a ``MappingProxyType`` raises ``TypeError``,
which sits inside the ``except`` tuple below, so every token would report as
unreadable with the real cause hidden. Verified 2026-08-26.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.types import Options

from fantabot.domain.tokens.errors import TokenUnreadable

# The `Options` annotation is load-bearing, not decoration: PyJWT 2.13 types this
# parameter as a TypedDict, and `mypy --strict` rejects a bare `dict[str, bool]`
# with `Argument "options" has incompatible type`. Verified 2026-08-26 — and
# `jwt.api_jwt.Options` does NOT work (not explicitly exported); `jwt.types` does.
#
# The whole point is to read `exp` on an expired token, so verify_exp must be off
# too — with it on, PyJWT raises before handing back the payload and
# `auth status` could never print the date that explains the failure.
#
# No `algorithms=` argument: the real header is `RS256` with a `kid` (measured
# 2026-08-26), and naming an algorithm we are not checking would only be a lie
# that breaks the day the issuer rotates.
_DECODE_OPTIONS: Options = {"verify_signature": False, "verify_exp": False}

DEFAULT_SKEW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The claims fantabot acts on, from ``docs/leghe-api.md``.

    Deliberately does **not** hold the token. This value is passed around,
    logged about and put in error messages; the credential is not.
    """

    league_id: int  # l_id
    team_id: int | None  # t_id — this account's team in that lega
    user_id: int | None
    issued_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime, skew: timedelta = DEFAULT_SKEW) -> bool:
        """Dead, or close enough that a call started now would race the clock."""
        return now >= self.expires_at - skew

    def expires_in(self, now: datetime) -> timedelta:
        return self.expires_at - now


def _optional_int(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    return int(value) if value is not None else None


def decode_claims(token: str) -> TokenClaims:
    """Read a league token's payload. Raises ``TokenUnreadable``, never PyJWT's own.

    ``l_id`` arrives as a *string* in the real payload (``"4103937"``), not an
    int — see the decoded example in ``docs/leghe-api.md``. Coercing here means
    every caller compares ints, including the ``l_id`` gate that is the only
    check between a mislabelled row and acting in the wrong lega.
    """
    try:
        payload: dict[str, Any] = jwt.decode(token, options=_DECODE_OPTIONS)
        return TokenClaims(
            league_id=int(payload["l_id"]),
            team_id=_optional_int(payload, "t_id"),
            user_id=_optional_int(payload, "user_id"),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError, OverflowError) as exc:
        # `OverflowError` is not decoration and not a ValueError subclass:
        # `datetime.fromtimestamp(10**20, tz=UTC)` raises it, in both directions,
        # so an absurd `exp` would escape as a raw traceback and break this
        # function's own promise. Verified 2026-08-26.
        #
        # The token itself never reaches the message. A truncated or
        # wrong-format credential in a traceback is still a credential — and
        # `from None` keeps PyJWT's own message, which can quote the input, out
        # of the chain.
        raise TokenUnreadable(f"not a readable league token: {type(exc).__name__}") from None
