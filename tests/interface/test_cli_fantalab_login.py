"""`fantabot auth fantalab-login`, with a fake browser.

No Chromium launches and no socket opens — the autouse guard would fail these
outright. What is pinned is the posture: the page is navigated once and never
interacted with, no credential reaches the output, and no session file is
written.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fantabot.application import login_wait
from fantabot.application.fantalab_login import FantalabLoginResult, run
from fantabot.interface.console import console


def _confirm(_message: str) -> str:
    """Stands in for the human clicking Continue / pressing Enter."""
    return ""


def _read_state(ctx: Any) -> dict[str, Any]:
    """What `adapters.browser.capture.read_storage_state` does, without playwright."""
    return dict(ctx.storage_state())


@pytest.fixture(autouse=True)
def _instant_poll(monkeypatch: Any) -> None:
    """No real waiting. The capture loop polls; the suite must still sleep zero seconds."""
    monkeypatch.setattr(login_wait, "POLL_INTERVAL_S", 0.0)


ANSI = re.compile(r"\x1b\[[0-9;]*m")

REFRESH = "refresh-CanaryValue777"
STORAGE = {
    "origins": [
        {
            "origin": "https://app.fantalab.it",
            "localStorage": [
                {"name": "refresh_token", "value": REFRESH},
                {"name": "id_token", "value": "id-CanaryValue777"},
                {"name": "access_token", "value": "access-CanaryValue777"},
                {"name": "user_id", "value": "user-uuid"},
            ],
        }
    ]
}


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def goto(self, url: str) -> None:
        self.calls.append("goto")

    def __getattr__(self, name: str) -> Any:
        def record(*_a: Any, **_k: Any) -> None:
            self.calls.append(name)

        return record


class _FakeContext:
    def __init__(self, storage: dict[str, Any]) -> None:
        self.page = _FakePage()
        self._storage = storage

    def new_page(self) -> _FakePage:
        return self.page

    def storage_state(self) -> dict[str, Any]:
        return self._storage

    def __enter__(self) -> _FakeContext:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def _clean_session(db_session: Any) -> None:  # pragma: no cover - db tier only
    return None


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ctx: _FakeContext) -> Any:
    """Run the flow with the database and cipher stubbed out."""
    import fantabot.application.fantalab_login as module

    saved: list[Any] = []

    class _Cipher:
        fingerprint = "abcd1234"

        def encrypt(self, plaintext: str) -> bytes:
            return plaintext.encode()

    class _Store:
        def __init__(self, *_a: Any) -> None: ...

        def describe(self) -> list[Any]:
            return []

        def save(self, captured: Any, **_k: Any) -> None:
            saved.append(captured)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_e: object) -> None: ...

        def commit(self) -> None: ...

    monkeypatch.setattr(module, "_preflight_key", lambda: _Cipher())
    monkeypatch.setattr(module, "_preflight_database", lambda: None)
    monkeypatch.setitem(
        __import__("sys").modules, "fantabot.adapters.tokens.fantalab_store",
        type("m", (), {"FantalabStore": _Store})(),
    )
    import fantabot.adapters.persistence as db_module

    monkeypatch.setattr(db_module.database_manager, "get_session", lambda: _Session())
    result = run(report=console, browser_factory=lambda: ctx, read_state=_read_state,
                 now=datetime(2026, 8, 27, tzinfo=UTC), prompt=_confirm)
    return result, saved


def test_the_page_is_navigated_once_and_never_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A scripted sign-in is what gets accounts flagged. The recorded call list
    is asserted exactly, so any future click fails this rather than shipping.

    Precise about what it guarantees, now that the capture polls. The claim is that
    this program never clicks, types into, or navigates the login page — not that no
    script runs anywhere in the browser. `storage_state()` is a context-channel call
    that evaluates a collector in Chromium's *isolated utility world*, invisible to the
    site's own JavaScript. It does not reach this Page object, which is what the rule
    is actually about."""
    ctx = _FakeContext(STORAGE)
    _run(tmp_path, monkeypatch, ctx)
    assert ctx.page.calls == ["goto"], f"the page was interacted with: {ctx.page.calls}"


def test_no_credential_reaches_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cron captures stdout, so anything printed outlives the run in a log."""
    _run(tmp_path, monkeypatch, _FakeContext(STORAGE))
    output = ANSI.sub("", capsys.readouterr().out)
    assert "CanaryValue777" not in output
    assert "user-uuid" in output, "the account id is not a secret and is worth reporting"


def test_the_session_is_captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, saved = _run(tmp_path, monkeypatch, _FakeContext(STORAGE))
    assert isinstance(result, FantalabLoginResult)
    assert result.stored and result.browser_opened
    assert saved[0].refresh_token == REFRESH


def test_no_session_file_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage_state.json would hold three credentials in the clear — the very
    thing the token-store phase exists to prevent."""
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, monkeypatch, _FakeContext(STORAGE))
    assert list(tmp_path.rglob("*.json")) == []


class TestThePreflightDsnMatchesTheLegaLoginsExactly:
    """The two login screens print one DSN, rendered by `config_report.safe_dsn`.

    Both called `make_url(...).render_as_string(hide_password=True)` until 2026-09-24 —
    the idiom `application/config_report.py` exists to replace, which percent-encodes the
    socket path and masks an *empty* password as `***`. `tests/interface/test_cli_login.py`
    pins the three shapes; what is pinned here is that this copy did not stay behind. A
    per-module literal would pass while the two screens disagreed, so the assertion is the
    other preflight's own output.
    """

    @staticmethod
    def _line(monkeypatch: pytest.MonkeyPatch, module: Any, dsn: str) -> str:
        from sqlalchemy.exc import OperationalError

        from fantabot import config
        from fantabot.adapters.persistence import database_manager
        from fantabot.domain.tokens.errors import LoginAborted

        monkeypatch.setattr(config.settings, "fantabot_database_url", dsn)

        def boom() -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        monkeypatch.setattr(database_manager, "get_session", lambda: boom())

        with pytest.raises(LoginAborted) as caught:
            module._preflight_database()
        return str(caught.value).splitlines()[0]

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata",
            "postgresql+psycopg2://u:S3cr3tCanary@localhost:54321/fantabot",
            "postgresql+psycopg2://postgres@localhost:5432/fantabot",
        ],
        ids=["bundled-socket", "real-password", "no-password"],
    )
    def test_both_preflights_say_the_same_thing(
        self, monkeypatch: pytest.MonkeyPatch, dsn: str
    ) -> None:
        from fantabot.application import auth_login, fantalab_login

        mine = self._line(monkeypatch, fantalab_login, dsn)
        theirs = self._line(monkeypatch, auth_login, dsn)

        assert mine == theirs
        assert "S3cr3tCanary" not in mine
        assert "%2F" not in mine


# -- the Typer body -----------------------------------------------------------------------
#
# Everything above drives `application.fantalab_login.run` directly. The command that calls
# it was measured 2026-09-24 at **0 of 11 body statements** — so the posture the file's own
# docstring describes was pinned one layer below where an operator invokes it, and the
# wiring that decides whether a real Chromium opens was itself untested.


class TestFantalabLoginBody:
    """The Typer body, with `run` faked. No browser is constructed; the autouse socket
    guard would fail these outright if one were."""

    @staticmethod
    def _wire(monkeypatch: Any) -> dict[str, Any]:
        from fantabot.adapters.browser import capture
        from fantabot.application import fantalab_login as use_case

        seen: dict[str, Any] = {"kwargs": None, "browsers": [], "raises": None}

        def _run(**kwargs: Any) -> Any:
            seen["kwargs"] = kwargs
            if seen["raises"] is not None:
                raise seen["raises"]
            return FantalabLoginResult(user_id="u1", browser_opened=True, stored=True)

        # `real_browser` is replaced with a recorder, never called by these tests — what is
        # asserted is the argument the body would hand it, without launching Chromium.
        monkeypatch.setattr(
            capture, "real_browser", lambda name=None: seen["browsers"].append(name)
        )
        monkeypatch.setattr(use_case, "run", _run)
        return seen

    def test_a_clean_login_exits_0_and_hands_over_both_seams(
        self, monkeypatch: Any
    ) -> None:
        """The command supplies a `browser_factory` and a `read_state` and does nothing
        itself — the rule that this program types nothing and clicks nothing is kept by
        having no page to type into on this side of the call."""
        from typer.testing import CliRunner

        from fantabot.adapters.browser.capture import read_storage_state
        from fantabot.interface.app import app

        seen = self._wire(monkeypatch)

        result = CliRunner().invoke(app, ["auth", "fantalab-login"])

        assert result.exit_code == 0
        assert seen["kwargs"]["read_state"] is read_storage_state
        assert callable(seen["kwargs"]["browser_factory"])
        assert seen["kwargs"]["force"] is False
        # Not opened: the body builds the factory and hands it over uncalled.
        assert seen["browsers"] == []

    def test_an_empty_browser_option_means_the_bundled_chromium(
        self, monkeypatch: Any
    ) -> None:
        """`real_browser(None)`, not `real_browser("")` — an empty string is a name, and
        Playwright would look for a channel called that."""
        from typer.testing import CliRunner

        from fantabot.interface.app import app

        seen = self._wire(monkeypatch)
        CliRunner().invoke(app, ["auth", "fantalab-login"])

        seen["kwargs"]["browser_factory"]()

        assert seen["browsers"] == [None]

    def test_a_named_browser_is_passed_through(self, monkeypatch: Any) -> None:
        from typer.testing import CliRunner

        from fantabot.interface.app import app

        seen = self._wire(monkeypatch)
        CliRunner().invoke(app, ["auth", "fantalab-login", "--browser", "msedge"])

        seen["kwargs"]["browser_factory"]()

        assert seen["browsers"] == ["msedge"]

    def test_force_reaches_the_use_case(self, monkeypatch: Any) -> None:
        from typer.testing import CliRunner

        from fantabot.interface.app import app

        seen = self._wire(monkeypatch)

        CliRunner().invoke(app, ["auth", "fantalab-login", "--force"])

        assert seen["kwargs"]["force"] is True

    def test_an_aborted_login_exits_with_the_code_the_exception_carries(
        self, monkeypatch: Any
    ) -> None:
        """`LoginAborted` exists to carry that number: a preflight refusal is not the same
        outcome as a window the operator closed, and a wrapper reads the difference."""
        from typer.testing import CliRunner

        from fantabot.domain.tokens.errors import LoginAborted
        from fantabot.interface.app import app

        seen = self._wire(monkeypatch)
        seen["raises"] = LoginAborted("no encryption key", code=3)

        result = CliRunner().invoke(app, ["auth", "fantalab-login"])

        assert result.exit_code == 3
        assert "no encryption key" in result.output

    def test_a_closed_window_and_an_unreadable_capture_both_exit_1(
        self, monkeypatch: Any
    ) -> None:
        from typer.testing import CliRunner

        from fantabot.application.login_wait import CaptureUnreadable
        from fantabot.domain.tokens.errors import SignInWindowClosed
        from fantabot.interface.app import app

        for failure in (SignInWindowClosed(), CaptureUnreadable(4)):
            seen = self._wire(monkeypatch)
            seen["raises"] = failure

            result = CliRunner().invoke(app, ["auth", "fantalab-login"])

            assert result.exit_code == 1, failure
            assert result.output.strip(), failure
