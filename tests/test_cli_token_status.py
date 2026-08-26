"""`fantabot token-status` — the instrument, tested before the experiment exists.

The load-bearing case is SC 11: it must answer with `FANTABOT_ENCRYPTION_KEY`
absent. That is asserted **in process** here, not through a shell invocation —
`env -u FANTABOT_ENCRYPTION_KEY` does *not* unset it, because `config.py` sets
`env_file=".env"` and removing the environment variable simply falls through to
the dotenv file. A shell-based check would sign the criterion off green on a run
that had the key the whole time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _tokens
import httpx
import pytest
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from fantabot.cli import app, token_status_rows
from fantabot.db.models.tokens import LeagueToken
from fantabot.tokens.crypto import TokenCipher
from fantabot.tokens.status import TokenStatus
from fantabot.tokens.store import TokenStore

runner = CliRunner()
NOW = datetime(2026, 8, 26, tzinfo=UTC)
PLAINTEXT = _tokens.make_token(l_id=_tokens.LEGA_MANTRA)


def a_status(
    *,
    league_id: int = _tokens.LEGA_MANTRA,
    expires_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    key_fingerprint: str = "4f2a1c8e",
) -> TokenStatus:
    return TokenStatus(
        league_id=league_id,
        league_name="Legamiallerotaie2",
        key_fingerprint=key_fingerprint,
        issued_at=NOW - timedelta(days=7),
        expires_at=expires_at or NOW + timedelta(days=357),
        captured_at=NOW,
        last_seen_at=last_seen_at or NOW,
        last_verified_at=None,
    )


class _FakeStore:
    """A TokenStore stand-in: only `status`, `key_fingerprint`, `mark_verified`."""

    def __init__(self, rows: list[TokenStatus], fingerprint: str | None = "4f2a1c8e") -> None:
        self._rows = rows
        self.key_fingerprint = fingerprint
        self.verified: list[int] = []

    def status(self) -> list[TokenStatus]:
        return self._rows

    def mark_verified(self, league_id: int, at: datetime) -> None:
        self.verified.append(league_id)


def _store(*rows: TokenStatus, fingerprint: str | None = "4f2a1c8e") -> Any:
    return _FakeStore(list(rows), fingerprint)


# --- SC 11, proved without a shell ----------------------------------------


def test_expiry_is_reported_with_no_key_at_all() -> None:
    """SC 11. The plaintext expiry columns are what make this possible."""
    rows = token_status_rows(_store(a_status(), fingerprint=None), now=NOW)

    assert rows[0][2] == f"{(NOW + timedelta(days=357)):%Y-%m-%d}"
    assert rows[0][3] == "ok (357d)"


def test_a_real_store_with_no_cipher_answers_too() -> None:
    """The same criterion against the real TokenStore, not just the fake."""

    class _Result:
        def __init__(self, v: Any) -> None:
            self._v = v

        def all(self) -> Any:
            return self._v

        def scalar_one_or_none(self) -> Any:
            return None

    class _Session:
        def execute(self, statement: Any, params: Any = None) -> Any:
            return _Result(
                [
                    (
                        _tokens.LEGA_MANTRA,
                        "Legamiallerotaie2",
                        "4f2a1c8e",
                        NOW,
                        NOW + timedelta(days=357),
                        NOW,
                        NOW,
                        None,
                        None,
                        None,
                    )
                ]
            )

    store = TokenStore(_Session(), cipher=None)

    assert token_status_rows(store, now=NOW)[0][3] == "ok (357d)"


# --- the four states ------------------------------------------------------


def test_an_expired_row_shows_expired() -> None:
    rows = token_status_rows(_store(a_status(expires_at=NOW - timedelta(days=1))), now=NOW)

    assert rows[0][3].startswith("EXPIRED")


def test_a_key_mismatch_shows_both_fingerprints() -> None:
    """SC 15."""
    rows = token_status_rows(_store(a_status(key_fingerprint="9b30d7a1")), now=NOW)

    assert rows[0][3] == "KEY MISMATCH (row 9b30d7a1, .env 4f2a1c8e)"


def test_a_lagging_row_shows_orphaned() -> None:
    """SC 12."""
    rows = token_status_rows(
        _store(
            a_status(league_id=3584692, last_seen_at=NOW - timedelta(days=175)),
            a_status(league_id=4103937),
        ),
        now=NOW,
    )

    assert rows[0][3].startswith("ORPHANED")
    assert rows[1][3] == "ok (357d)"


# --- --verify -------------------------------------------------------------


def test_without_verify_no_request_is_ever_built() -> None:
    """Proven with a transport that fails the test if it is touched."""

    def explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("token-status made a request without --verify")

    token_status_rows(
        _store(a_status()), now=NOW, verify=False, transport=httpx.MockTransport(explode)
    )


def test_verify_issues_exactly_one_request_per_row_and_records_it() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"sId": 21, "mday": 2})

    store = _store(a_status(league_id=3584692), a_status(league_id=4103937))
    store.load_plaintext = lambda league_id, now=None: PLAINTEXT  # type: ignore[attr-defined]

    rows = token_status_rows(
        store, now=NOW, verify=True, transport=httpx.MockTransport(handler)
    )

    assert len(seen) == 2
    assert store.verified == [3584692, 4103937]
    assert all("verified" in row[3] for row in rows)


def test_a_rejected_token_is_reported_in_the_row_and_does_not_raise() -> None:
    """One dead lega must not take the whole table down."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "ATH001"})

    store = _store(a_status())
    store.load_plaintext = lambda league_id, now=None: PLAINTEXT  # type: ignore[attr-defined]

    rows = token_status_rows(
        store, now=NOW, verify=True, transport=httpx.MockTransport(handler)
    )

    assert "fantabot login" in rows[0][3]
    assert store.verified == []
    # "ok (357d) · apileague rejected the token" reads as a contradiction —
    # observed on a real run against the live API.
    assert not rows[0][3].startswith("ok"), f"contradictory state: {rows[0][3]}"
    assert rows[0][3].startswith("REJECTED")


# --- the command shell ----------------------------------------------------


def test_an_empty_table_says_to_log_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no lega configured and no rows, there is nothing to call MISSING.

    `fantabot_league_id` is pinned to 0 rather than inherited: this test read the
    developer's own `.env`, and passed only because that value happened to be
    unset. Setting it to a real lega turned the output into a MISSING row and
    broke the test for a reason that had nothing to do with the code.
    """
    from fantabot import config
    from fantabot.db import database_manager

    monkeypatch.setattr(config.settings, "fantabot_league_id", 0)
    monkeypatch.setattr(database_manager, "_session_factory", lambda: _EmptySession())
    result = runner.invoke(app, ["token-status"])

    assert result.exit_code == 0
    assert "No tokens stored" in result.output


def test_a_configured_league_with_no_row_is_reported_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC 11's MISSING half, via `.env` rather than `--league`.

    A lega is *known* if `fantabot_league_id` names it, so an empty table stops
    saying "nothing stored" and starts naming the lega that is absent.
    """
    from fantabot import config
    from fantabot.db import database_manager

    monkeypatch.setattr(config.settings, "fantabot_league_id", 4103937)
    monkeypatch.setattr(database_manager, "_session_factory", lambda: _EmptySession())
    result = runner.invoke(app, ["token-status"])

    assert result.exit_code == 0
    assert "MISSING" in result.output
    assert "4103937" in result.output


def test_an_unknown_league_is_reported_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC 11's MISSING half. A lega is *known* only if you named it."""
    from fantabot.db import database_manager

    monkeypatch.setattr(database_manager, "_session_factory", lambda: _EmptySession())
    result = runner.invoke(app, ["token-status", "--league", "9911111"])

    assert result.exit_code == 0
    assert "MISSING" in result.output
    assert "9911111" in result.output


def test_a_dead_database_prints_an_instruction_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    from fantabot.db import database_manager

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database_manager, "_session_factory", lambda: boom())
    result = runner.invoke(app, ["token-status"])

    assert result.exit_code == 1
    assert "docker compose up -d" in result.output
    assert "postgres:postgres@" not in result.output


def test_no_output_contains_a_token_or_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot import config
    from fantabot.db import database_manager

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config.settings, "fantabot_encryption_key", key)
    monkeypatch.setattr(database_manager, "_session_factory", lambda: _EmptySession())

    result = runner.invoke(app, ["token-status"])

    assert key[:8] not in result.output
    assert PLAINTEXT[:12] not in result.output


class _EmptySession:
    def __enter__(self) -> _EmptySession:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def execute(self, statement: Any, params: Any = None) -> Any:
        class _R:
            def all(self) -> list[Any]:
                return []

            def scalar_one_or_none(self) -> None:
                return None

        return _R()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_the_stored_row_type_never_carries_a_ciphertext() -> None:
    """Nothing that renders a status needs one, so nothing gets one."""
    assert not hasattr(a_status(), "ciphertext")


def test_a_fake_league_token_is_never_constructed_with_a_real_token() -> None:
    """Guards the fixtures themselves: every token in this file is synthesized."""
    cipher = TokenCipher(Fernet.generate_key().decode())
    row = LeagueToken(
        league_id=1,
        ciphertext=cipher.encrypt(PLAINTEXT),
        key_fingerprint=cipher.fingerprint,
        issued_at=NOW,
        expires_at=NOW,
    )

    assert PLAINTEXT.encode() not in row.ciphertext


def test_the_verify_flag_actually_reaches_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """It did not, and no unit test noticed.

    Every `--verify` test called `token_status_rows(..., verify=True)` directly,
    so the *wiring* from the Typer command was never covered — and the command
    dropped the flag on the floor. `fantabot token-status --verify` made no
    request at all. Found by running the real binary against a real database,
    not by the suite.
    """
    from fantabot import cli
    from fantabot.db import database_manager

    seen: dict[str, Any] = {}

    def spy(store: Any, *, now: Any, verify: bool = False, transport: Any = None) -> list[Any]:
        seen["verify"] = verify
        return []

    monkeypatch.setattr(cli, "token_status_rows", spy)
    monkeypatch.setattr(database_manager, "_session_factory", lambda: _EmptySession())

    runner.invoke(app, ["token-status"])
    assert seen["verify"] is False

    runner.invoke(app, ["token-status", "--verify"])
    assert seen["verify"] is True, "--verify never reached token_status_rows"
