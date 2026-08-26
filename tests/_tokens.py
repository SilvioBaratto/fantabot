"""Synthesize league tokens for tests. A real one never enters this repository.

SPEC's Always list: "Synthesize test tokens. Never commit a real one, expired or
not." This module is how that stays the convenient path — a fixture built from
the documented shape, never produced by redacting a real blob, because a
redaction bug commits a credential.

Imported as ``import _tokens``, **not** ``from tests._tokens import ...``. There
is no ``tests/__init__.py`` (only ``tests/integration/`` has one), so under
pytest's default prepend import mode ``tests/`` goes on ``sys.path`` and
``tests.`` is not a package prefix. ``tests/integration/`` reaches it the same
way. Getting this wrong is a collection error, not a test failure.

**The signature segment is ``"c2ln"`` and that is measured, not arbitrary.** On
PyJWT 2.13.0 in this venv, a *one-character* third segment raises
``DecodeError: Invalid crypto padding`` even with ``verify_signature=False``,
while ``""``, ``"xx"``, ``"xxx"``, ``"xxxx"`` and ``"c2ln"`` all decode. A
fixture that tripped the padding check would make every claims test fail for a
reason that has nothing to do with the code under test.

The shape mirrors the real payload recorded in ``docs/leghe-api.md`` — including
``nbf``, which that document's own decoded example predates.
"""

from __future__ import annotations

import base64
import json
from typing import Any

SIGNATURE = "c2ln"

# The real header, measured 2026-08-26: RS256 with a key id.
DEFAULT_HEADER: dict[str, Any] = {"alg": "RS256", "kid": "k1", "typ": "JWT"}

# Both leghe on the account, and the moment their tokens were minted.
LEGA_CLASSIC = 3584692
LEGA_MANTRA = 4103937
TEAM_CLASSIC = 17128426
TEAM_MANTRA = 10000003
USER_ID = 20000003
IAT = 1787129066  # 2026-08-19
EXP = 1818665066  # 2027-08-19, a 365-day lifetime


def b64url(payload: dict[str, Any]) -> str:
    """Base64url with the padding stripped, as a JWT segment is written."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_token(
    *,
    l_id: int | str,
    t_id: int | str | None = None,
    user_id: int | str | None = USER_ID,
    iat: int = IAT,
    exp: int = EXP,
    header: dict[str, Any] | None = None,
    signature: str = SIGNATURE,
    **extra: Any,
) -> str:
    """A synthesized league token.

    ``l_id``/``t_id``/``user_id`` are stringified because the real payload
    carries them as strings — see ``docs/leghe-api.md``'s decoded example.
    """
    payload: dict[str, Any] = {
        "iss": "https://leghe.fantacalcio.it",
        "iat": iat,
        "exp": exp,
        "nbf": iat,
        "l_id": str(l_id),
        "user_id": str(user_id) if user_id is not None else None,
        "role": "user_league",
        "token_use": "id",
        "aud": "fantacalcio",
    }
    if t_id is not None:
        payload["t_id"] = str(t_id)
    if user_id is None:
        payload.pop("user_id")
    payload.update(extra)
    return f"{b64url(header or DEFAULT_HEADER)}.{b64url(payload)}.{signature}"


def raw_token(payload_segment: str, *, signature: str = SIGNATURE) -> str:
    """A token whose payload segment is supplied verbatim — for malformed cases."""
    return f"{b64url(DEFAULT_HEADER)}.{payload_segment}.{signature}"


def storage_state(
    *,
    leagues: list[dict[str, Any]] | None = None,
    user_id: int = USER_ID,
    origin: str = "https://leghe.fantacalcio.it",
    include_blob: bool = True,
) -> dict[str, Any]:
    """A Playwright ``storage_state()`` dict shaped like the real one.

    Measured 2026-08-26: the blob lives under ``localStorage`` for the
    ``leghe.fantacalcio.it`` origin, ``current-user`` holds the numeric id, and
    ``currentLeague`` is a *copy* of whichever ``leagues[]`` entry is active —
    not a separate credential.
    """
    if leagues is None:
        leagues = [
            {
                "id": LEGA_CLASSIC,
                "name": "Legamiallerotaie",
                "alias": "legamiallerotaie",
                "type": 1,
                "token": make_token(l_id=LEGA_CLASSIC, t_id=TEAM_CLASSIC),
            },
            {
                "id": LEGA_MANTRA,
                "name": "Legamiallerotaie2",
                "alias": "legamiallerotaie2",
                "type": 2,
                "token": make_token(l_id=LEGA_MANTRA, t_id=TEAM_MANTRA),
            },
        ]

    blob: dict[str, Any] = {
        "version": "1",
        "current-user": user_id,
        f"current-user-{user_id}": {
            "id": user_id,
            "username": "tester",
            "email": "tester@example.test",
            "token": "x" * 128,
            "jwt": "y" * 809,
            "leagues": leagues,
            "currentLeague": dict(leagues[-1]) if leagues else None,
        },
    }

    entries = [{"name": "LEAGUES2024_LOCAL", "value": json.dumps(blob)}] if include_blob else []
    return {
        "cookies": [],
        "origins": [{"origin": origin, "localStorage": [*entries, {"name": "_cc_id", "value": "1"}]}],
    }
