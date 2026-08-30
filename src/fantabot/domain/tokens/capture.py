"""A browser session blob into league-checked tokens. Pure: dict in, values out.

The walk, from ``docs/leghe-api.md``::

    storage_state → origins[] → the leghe.fantacalcio.it entry
                  → localStorage[] → LEAGUES2024_LOCAL → json.loads
                  → current-user → current-user-{uid} → leagues[]
                  → for each: decode the token, assert l_id == entry id

Measured 2026-08-26, and the reason no page is ever clicked: **every entry in
``leagues[]`` carries its own working token.** ``currentLeague`` is a copy of
whichever entry is active, not a separate credential. One read gets every lega.

Two details a parser written from memory gets wrong. Playwright's
``localStorage`` is a **list of ``{"name", "value"}`` pairs, not a dict**. And
``l_id`` arrives as a string.

The parameter is typed ``Mapping[str, Any]``, never Playwright's ``StorageState``:
that TypedDict lives in ``playwright._impl._api_structures``, a private module,
and importing it would put Playwright and a private-API dependency on a module
SPEC declares pure. ``login.py`` passes ``dict(ctx.storage_state())`` across the
boundary.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from fantabot.domain.tokens.claims import TokenClaims, decode_claims
from fantabot.domain.tokens.errors import LeagueMismatch, NoLeaguesFound

ORIGIN = "https://leghe.fantacalcio.it"
STORAGE_KEY = "LEAGUES2024_LOCAL"


@dataclass(frozen=True, slots=True)
class CapturedToken:
    """One lega's token, straight out of the browser and not yet encrypted.

    ``token`` is ``field(repr=False)`` *and* the class defines its own
    ``__repr__``. Belt and braces on purpose: this is the only object in the
    phase that holds a plaintext credential, and a frozen dataclass's generated
    repr prints every field — into pytest failure output, into every traceback,
    into any ``console.print`` of the list. **SPEC's secrecy list names only
    ``LeagueToken.__repr__`` and misses this entirely.**
    """

    league_id: int
    league_name: str | None
    token: str = field(repr=False)
    claims: TokenClaims = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"<CapturedToken lega={self.league_id} "
            f"exp={self.claims.expires_at:%Y-%m-%d}>"
        )


def _storage_entries(state: Mapping[str, Any], origin: str) -> Sequence[Mapping[str, Any]]:
    """The localStorage pairs for one origin, matched on host not on string."""
    wanted = urlsplit(origin).netloc or origin
    origins = state.get("origins")
    if not isinstance(origins, Sequence) or isinstance(origins, str | bytes):
        raise NoLeaguesFound()

    for candidate in origins:
        if not isinstance(candidate, Mapping):
            continue
        if (urlsplit(str(candidate.get("origin", ""))).netloc or "") != wanted:
            continue
        entries = candidate.get("localStorage")
        # A list of {"name", "value"} pairs. A dict here means someone
        # "simplified" the shape and the walk would silently find nothing.
        if isinstance(entries, Sequence) and not isinstance(entries, str | bytes):
            return [e for e in entries if isinstance(e, Mapping)]
        raise NoLeaguesFound()
    raise NoLeaguesFound()


def _blob(state: Mapping[str, Any], origin: str) -> Mapping[str, Any]:
    for entry in _storage_entries(state, origin):
        if entry.get("name") != STORAGE_KEY:
            continue
        try:
            parsed = json.loads(str(entry.get("value", "")))
        except (json.JSONDecodeError, TypeError) as exc:
            raise NoLeaguesFound() from exc
        if not isinstance(parsed, Mapping):
            raise NoLeaguesFound()
        return parsed
    raise NoLeaguesFound()


def _leagues(blob: Mapping[str, Any]) -> tuple[Sequence[Mapping[str, Any]], Any]:
    """The `leagues[]` array and the `current-user` pointer beside it."""
    uid = blob.get("current-user")
    if uid is None:
        raise NoLeaguesFound()

    user = blob.get(f"current-user-{uid}")
    if not isinstance(user, Mapping):
        raise NoLeaguesFound()

    leagues = user.get("leagues")
    if not isinstance(leagues, Sequence) or isinstance(leagues, str | bytes) or not leagues:
        raise NoLeaguesFound()

    entries = [entry for entry in leagues if isinstance(entry, Mapping)]
    if len(entries) != len(leagues):
        raise NoLeaguesFound()
    return entries, uid


def parse_storage_state(
    state: Mapping[str, Any], *, origin: str = ORIGIN
) -> list[CapturedToken]:
    """Every lega's token, or nothing at all.

    Fail-closed, in ``mantra_grid/gates.py`` posture: a failed gate returns
    nothing, and the output is never patched to satisfy a check.

    The ``l_id`` gate refuses the **entire** capture rather than skipping the
    offending entry. A crossed id means the blob is not the shape we believe it
    is, and keeping the siblings we *think* are fine, out of a blob we have just
    proved we misunderstand, is the wrong side of that asymmetry.
    """
    blob = _blob(state, origin)
    entries, pointer = _leagues(blob)

    captured: list[CapturedToken] = []
    for entry in entries:
        entry_id, raw = entry.get("id"), entry.get("token")
        if entry_id is None or not isinstance(raw, str) or not raw:
            raise NoLeaguesFound()

        claims = decode_claims(raw)
        if claims.league_id != int(entry_id):
            # SC 10, and the only check between a mislabelled row and bidding
            # in the wrong lega. Neither id is a secret; the token is not named.
            raise LeagueMismatch(int(entry_id), claims.league_id)

        _cross_check_user(claims, pointer)
        captured.append(
            CapturedToken(
                league_id=claims.league_id,
                league_name=_optional_str(entry.get("name")),
                token=raw,
                claims=claims,
            )
        )
    return captured


def _optional_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _cross_check_user(claims: TokenClaims, pointer: Any) -> None:
    """Warn, never refuse.

    A ``user_id`` disagreeing with the blob's ``current-user`` pointer usually
    means a shared browser profile or the wrong account — worth saying out loud,
    but SPEC's Always list names exactly one assertion, and a refusal SPEC did
    not ask for can block a legitimate capture with no way to override it.
    """
    if claims.user_id is None or pointer is None:
        return
    try:
        expected = int(pointer)
    except (TypeError, ValueError):
        return
    if claims.user_id != expected:
        warnings.warn(
            f"lega {claims.league_id}'s token has user_id {claims.user_id} but the "
            f"session belongs to {expected} — a shared browser profile, or the "
            "wrong account signed in. Captured anyway.",
            UserWarning,
            stacklevel=2,
        )
