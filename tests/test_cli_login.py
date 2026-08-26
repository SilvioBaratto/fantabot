"""`fantabot login` — the decision table, with a fake browser and no sockets.

The preflight is the half worth pinning hardest. A password typed into a real
browser, possibly behind a captcha, is expensive to waste, so everything
checkable is checked before the browser opens — and "the browser never opened"
is asserted on the fake's own flag, not inferred from an exit code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fantabot import login
from fantabot.login import LoginAborted
from fantabot.tokens.status import TokenStatus

NOW = datetime(2026, 8, 26, tzinfo=UTC)
GOOD_KEY = "8B7z0LQ1cVQ0yZ0Xh3n4WQ1mJ5rT2vK8sN6pA9dF0cE="


class _FakeBrowser:
    """Records whether it was ever entered. That flag is the assertion."""

    def __init__(self) -> None:
        self.entered = False

    def __call__(self) -> _FakeBrowser:
        return self

    def __enter__(self) -> _FakeBrowser:
        self.entered = True
        return self

    def __exit__(self, *args: Any) -> None:
        return None


def a_status(*, league_id: int = 4103937, expires_at: datetime | None = None) -> TokenStatus:
    return TokenStatus(
        league_id=league_id,
        league_name="Legamiallerotaie2",
        key_fingerprint="4f2a1c8e",
        issued_at=NOW - timedelta(days=7),
        expires_at=expires_at or NOW + timedelta(days=357),
        captured_at=NOW,
        last_seen_at=NOW,
        last_verified_at=None,
    )


@pytest.fixture
def stub_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A reachable database whose stored rows the test controls."""
    rows: list[TokenStatus] = []
    writes: list[Any] = []

    monkeypatch.setattr(login, "_preflight_database", lambda: None)

    class _Store:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def status(self) -> list[TokenStatus]:
            return rows

        def save(self, captured: Any, *, now: Any) -> int:
            writes.append(captured)
            return len(captured)

    import fantabot.tokens.store as store_module

    monkeypatch.setattr(store_module, "TokenStore", _Store)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    from fantabot.db import database_manager

    monkeypatch.setattr(database_manager, "get_session", lambda: _Session())

    return {"rows": rows, "writes": writes}


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", GOOD_KEY)


# --- SC 1 and SC 2: refused before the browser ----------------------------


def test_a_missing_key_exits_two_and_opens_no_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC 1. Asserted on the fake's flag, not on the exit code."""
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "")
    browser = _FakeBrowser()

    with pytest.raises(LoginAborted) as caught:
        login.run(browser_factory=browser, now=NOW)

    assert caught.value.code == 2
    assert browser.entered is False
    assert "Fernet.generate_key()" in str(caught.value)
    assert "Nothing was opened and nothing was written" in str(caught.value)


def test_a_malformed_key_names_the_shape_and_opens_no_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC 2."""
    from fantabot import config

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "not-a-key")
    browser = _FakeBrowser()

    with pytest.raises(LoginAborted) as caught:
        login.run(browser_factory=browser, now=NOW)

    assert browser.entered is False
    assert "44-character urlsafe-base64" in str(caught.value)


def test_an_unreachable_database_exits_before_the_browser(
    monkeypatch: pytest.MonkeyPatch, with_key: None
) -> None:
    """SC 1's other half, and the reason the preflight exists at all."""
    from sqlalchemy.exc import OperationalError

    from fantabot.db import database_manager

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database_manager, "get_session", lambda: boom())
    browser = _FakeBrowser()

    with pytest.raises(LoginAborted) as caught:
        login.run(browser_factory=browser, now=NOW)

    assert browser.entered is False
    message = str(caught.value)
    assert "docker compose up -d" in message
    assert "Nothing was opened and nothing was written" in message


def test_the_database_error_never_prints_the_dsn_password(
    monkeypatch: pytest.MonkeyPatch, with_key: None
) -> None:
    from sqlalchemy.exc import OperationalError

    from fantabot import config
    from fantabot.db import database_manager

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://u:S3cr3tCanary@localhost:54321/fantabot",
    )

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("nope"))

    monkeypatch.setattr(database_manager, "get_session", lambda: boom())

    with pytest.raises(LoginAborted) as caught:
        login.run(browser_factory=_FakeBrowser(), now=NOW)

    assert "S3cr3tCanary" not in str(caught.value)


# --- SC 7, 8, 9: when the browser does and does not open ------------------


def test_all_tokens_valid_opens_no_browser(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC 7."""
    stub_db["rows"].extend([a_status(league_id=3584692), a_status(league_id=4103937)])
    browser = _FakeBrowser()

    result = login.run(browser_factory=browser, now=NOW)

    assert browser.entered is False
    assert result.browser_opened is False
    assert "No browser opened" in capsys.readouterr().out


def test_an_expired_token_opens_the_browser(stub_db: Any, with_key: None) -> None:
    """SC 8."""
    stub_db["rows"].append(a_status(expires_at=NOW - timedelta(days=1)))
    browser = _FakeBrowser()

    with pytest.raises(NotImplementedError):
        login.run(browser_factory=browser, now=NOW)


def test_force_opens_the_browser_even_when_everything_is_valid(
    stub_db: Any, with_key: None
) -> None:
    """SC 9."""
    stub_db["rows"].append(a_status())

    with pytest.raises(NotImplementedError):
        login.run(browser_factory=_FakeBrowser(), force=True, now=NOW)


def test_an_empty_table_opens_the_browser(stub_db: Any, with_key: None) -> None:
    with pytest.raises(NotImplementedError):
        login.run(browser_factory=_FakeBrowser(), now=NOW)


def test_league_restricts_which_leghe_must_be_valid(stub_db: Any, with_key: None) -> None:
    """A valid 4103937 beside an expired 3584692: `--league 4103937` is a no-op."""
    stub_db["rows"].extend(
        [
            a_status(league_id=3584692, expires_at=NOW - timedelta(days=1)),
            a_status(league_id=4103937),
        ]
    )
    browser = _FakeBrowser()

    result = login.run(browser_factory=browser, league=4103937, now=NOW)

    assert browser.entered is False
    assert result.browser_opened is False


def test_league_naming_an_expired_lega_opens_the_browser(
    stub_db: Any, with_key: None
) -> None:
    stub_db["rows"].extend(
        [
            a_status(league_id=3584692, expires_at=NOW - timedelta(days=1)),
            a_status(league_id=4103937),
        ]
    )

    with pytest.raises(NotImplementedError):
        login.run(browser_factory=_FakeBrowser(), league=3584692, now=NOW)


def test_no_preflight_output_contains_the_key(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_db["rows"].append(a_status())

    login.run(browser_factory=_FakeBrowser(), now=NOW)

    assert GOOD_KEY[:8] not in capsys.readouterr().out
