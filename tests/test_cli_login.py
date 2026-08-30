"""`fantabot auth login` — the decision table, with a fake browser and no sockets.

The preflight is the half worth pinning hardest. A password typed into a real
browser, possibly behind a captcha, is expensive to waste, so everything
checkable is checked before the browser opens — and "the browser never opened"
is asserted on the fake's own flag, not inferred from an exit code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import _tokens
import pytest

from fantabot.application import auth_login as login
from fantabot.application.auth_login import LoginAborted
from fantabot.domain.tokens.status import TokenStatus
from fantabot.interface.console import console

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
    stamped: list[int] = []
    verified: list[int] = []

    monkeypatch.setattr(login, "_preflight_database", lambda: None)

    class _Store:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def status(self) -> list[TokenStatus]:
            return rows

        def save(self, captured: Any, *, now: Any) -> int:
            writes.append(captured)
            return len(captured)

        def touch_seen(self, league_ids: Any, at: Any) -> None:
            stamped.extend(league_ids)

        def load_plaintext(self, league_id: int, *, now: Any = None) -> str:
            return _tokens.make_token(l_id=league_id)

        def mark_verified(self, league_id: int, at: Any) -> None:
            verified.append(league_id)

    import fantabot.adapters.tokens.store as store_module

    monkeypatch.setattr(store_module, "TokenStore", _Store)

    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "get_session", lambda: _Session())

    return {"rows": rows, "writes": writes, "stamped": stamped, "verified": verified}


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
        login.run(report=console, browser_factory=browser, now=NOW)

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
        login.run(report=console, browser_factory=browser, now=NOW)

    assert browser.entered is False
    assert "44-character urlsafe-base64" in str(caught.value)


def test_an_unreachable_database_exits_before_the_browser(
    monkeypatch: pytest.MonkeyPatch, with_key: None
) -> None:
    """SC 1's other half, and the reason the preflight exists at all."""
    from sqlalchemy.exc import OperationalError

    from fantabot.adapters.persistence import database_manager

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(database_manager, "get_session", lambda: boom())
    browser = _FakeBrowser()

    with pytest.raises(LoginAborted) as caught:
        login.run(report=console, browser_factory=browser, now=NOW)

    assert browser.entered is False
    message = str(caught.value)
    assert "docker compose up -d" in message
    assert "Nothing was opened and nothing was written" in message


def test_the_database_error_never_prints_the_dsn_password(
    monkeypatch: pytest.MonkeyPatch, with_key: None
) -> None:
    from sqlalchemy.exc import OperationalError

    from fantabot import config
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://u:S3cr3tCanary@localhost:54321/fantabot",
    )

    def boom() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("nope"))

    monkeypatch.setattr(database_manager, "get_session", lambda: boom())

    with pytest.raises(LoginAborted) as caught:
        login.run(report=console, browser_factory=_FakeBrowser(), now=NOW)

    assert "S3cr3tCanary" not in str(caught.value)


# --- SC 7, 8, 9: when the browser does and does not open ------------------


def test_all_tokens_valid_opens_no_browser(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC 7."""
    stub_db["rows"].extend([a_status(league_id=3584692), a_status(league_id=4103937)])
    browser = _FakeBrowser()

    result = login.run(report=console, browser_factory=browser, now=NOW)

    assert browser.entered is False
    assert result.browser_opened is False
    assert "No browser opened" in capsys.readouterr().out


def test_an_expired_token_opens_the_browser(stub_db: Any, with_key: None) -> None:
    """SC 8."""
    stub_db["rows"].append(a_status(expires_at=NOW - timedelta(days=1)))
    ctx = _FakeContext()

    result = login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.entered is True
    assert result.browser_opened is True


def test_force_opens_the_browser_even_when_everything_is_valid(
    stub_db: Any, with_key: None
) -> None:
    """SC 9."""
    stub_db["rows"].append(a_status())
    ctx = _FakeContext()

    login.run(report=console, browser_factory=ctx, force=True, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.entered is True


def test_an_empty_table_opens_the_browser(stub_db: Any, with_key: None) -> None:
    ctx = _FakeContext()

    login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.entered is True


def test_league_restricts_which_leghe_must_be_valid(stub_db: Any, with_key: None) -> None:
    """A valid 4103937 beside an expired 3584692: `--league 4103937` is a no-op."""
    stub_db["rows"].extend(
        [
            a_status(league_id=3584692, expires_at=NOW - timedelta(days=1)),
            a_status(league_id=4103937),
        ]
    )
    browser = _FakeBrowser()

    result = login.run(report=console, browser_factory=browser, league=4103937, now=NOW)

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
    ctx = _FakeContext()

    login.run(report=console, 
        browser_factory=ctx, league=3584692, verify=False, prompt=_answers(""), now=NOW
    )

    assert ctx.entered is True


def test_no_preflight_output_contains_the_key(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    stub_db["rows"].append(a_status())

    login.run(report=console, browser_factory=_FakeBrowser(), now=NOW)

    assert GOOD_KEY[:8] not in capsys.readouterr().out


# --- T18: the browser step ------------------------------------------------


class _FakePage:
    """Records every method called on it. The recording *is* the assertion."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def goto(self, url: str) -> None:
        self.calls.append("goto")

    def __getattr__(self, name: str) -> Any:
        def recorder(*args: Any, **kwargs: Any) -> None:
            self.calls.append(name)

        return recorder


class _FakeContext:
    """A browser context that knows whether it has closed."""

    def __init__(self, blob: dict[str, Any] | None = None, fail_first: bool = False) -> None:
        self.blob = blob if blob is not None else _tokens.storage_state()
        self.closed = False
        self.entered = False
        self.page = _FakePage()
        self.reads = 0
        self._fail_first = fail_first

    def __call__(self) -> _FakeContext:
        return self

    def __enter__(self) -> _FakeContext:
        self.entered = True
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True

    def new_page(self) -> _FakePage:
        return self.page

    def storage_state(self) -> dict[str, Any]:
        if self.closed:
            raise AssertionError(
                "storage_state() was called after the context closed — it must be "
                "read inside the `with` body"
            )
        self.reads += 1
        if self._fail_first and self.reads == 1:
            return {"cookies": [], "origins": []}
        return self.blob


def _answers(*replies: str) -> Any:
    queue = list(replies)

    def prompt(message: str) -> str:
        return queue.pop(0) if queue else ""

    return prompt


def test_storage_state_is_read_before_the_context_closes(
    stub_db: Any, with_key: None
) -> None:
    """The single easiest way to break this task, invisible until a real login."""
    ctx = _FakeContext()

    login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.reads >= 1
    assert ctx.closed is True


def test_the_page_is_navigated_and_nothing_else(stub_db: Any, with_key: None) -> None:
    """SC 3's "no page interaction", as a tripwire rather than a promise.

    The surface most likely to accrete a `wait_for_selector` later is the one
    with no guard, so this asserts the recorded call list exactly.
    """
    ctx = _FakeContext()

    login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.page.calls == ["goto"], f"the page was interacted with: {ctx.page.calls}"


def test_both_leghe_are_stored(stub_db: Any, with_key: None) -> None:
    ctx = _FakeContext()

    result = login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert sorted(result.stored) == sorted([_tokens.LEGA_CLASSIC, _tokens.LEGA_MANTRA])


def test_a_crossed_l_id_stores_nothing(stub_db: Any, with_key: None) -> None:
    """SC 10, end to end: the gate refuses the whole capture."""
    from fantabot.domain.tokens.errors import LeagueMismatch

    crossed = _tokens.storage_state(
        leagues=[
            {
                "id": _tokens.LEGA_CLASSIC,
                "name": "x",
                "token": _tokens.make_token(l_id=_tokens.LEGA_MANTRA),
            }
        ]
    )
    ctx = _FakeContext(blob=crossed)

    with pytest.raises(LeagueMismatch):
        login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert stub_db["writes"] == []


def test_league_restricts_the_ciphertext_but_not_the_stamp(
    stub_db: Any, with_key: None
) -> None:
    """Without this split, `login --league X` falsely reports the other ORPHANED."""
    ctx = _FakeContext()

    result = login.run(report=console, 
        browser_factory=ctx,
        league=_tokens.LEGA_MANTRA,
        verify=False,
        prompt=_answers(""),
        now=NOW,
    )

    assert result.stored == [_tokens.LEGA_MANTRA]
    assert sorted(stub_db["stamped"]) == sorted([_tokens.LEGA_CLASSIC, _tokens.LEGA_MANTRA])


def test_league_naming_an_absent_lega_exits_nonzero_and_stores_nothing(
    stub_db: Any, with_key: None
) -> None:
    ctx = _FakeContext()

    with pytest.raises(LoginAborted) as caught:
        login.run(report=console, browser_factory=ctx, league=9911111, verify=False, prompt=_answers(""), now=NOW)

    assert caught.value.code == 1
    assert "9911111" in str(caught.value)
    assert stub_db["writes"] == []


def test_an_unparseable_blob_prompts_for_one_explicit_reread(
    stub_db: Any, with_key: None
) -> None:
    """The likeliest real failure: Enter pressed before the SPA finished writing."""
    ctx = _FakeContext(fail_first=True)

    result = login.run(report=console, 
        browser_factory=ctx, verify=False, prompt=_answers("", ""), now=NOW
    )

    assert ctx.reads == 2
    assert len(result.stored) == 2


# --- SC 6: the session file is opt-in -------------------------------------


def test_the_default_run_writes_no_session_file(
    stub_db: Any, with_key: None, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fantabot import config

    target = tmp_path / "storage_state.json"
    monkeypatch.setattr(config.settings, "fantabot_storage_state", target)
    monkeypatch.setattr(config.settings, "fantabot_data_dir", tmp_path)

    result = login.run(report=console, browser_factory=_FakeContext(), verify=False, prompt=_answers(""), now=NOW)

    assert target.exists() is False
    assert result.session_saved is False


def test_save_session_writes_it(
    stub_db: Any, with_key: None, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fantabot import config

    target = tmp_path / "storage_state.json"
    monkeypatch.setattr(config.settings, "fantabot_storage_state", target)
    monkeypatch.setattr(config.settings, "fantabot_data_dir", tmp_path)

    result = login.run(report=console, 
        browser_factory=_FakeContext(),
        verify=False,
        save_session=True,
        prompt=_answers(""),
        now=NOW,
    )

    assert target.exists() is True
    assert result.session_saved is True


def test_a_stale_session_file_is_warned_about_and_left_alone(
    stub_db: Any,
    with_key: None,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Deleting user data is on SPEC's Ask-first list."""
    from fantabot import config

    target = tmp_path / "storage_state.json"
    target.write_text("{}")
    before = target.stat().st_mtime_ns
    monkeypatch.setattr(config.settings, "fantabot_storage_state", target)
    monkeypatch.setattr(config.settings, "fantabot_data_dir", tmp_path)

    login.run(report=console, browser_factory=_FakeContext(), verify=False, prompt=_answers(""), now=NOW)

    assert target.stat().st_mtime_ns == before
    assert "left untouched" in capsys.readouterr().out


def test_no_printed_line_contains_a_token(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = _FakeContext()

    login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    output = capsys.readouterr().out
    for one in _tokens.storage_state()["origins"][0]["localStorage"]:
        if one["name"] != "LEAGUES2024_LOCAL":
            continue
        import json

        blob = json.loads(one["value"])
        for entry in blob[f"current-user-{_tokens.USER_ID}"]["leagues"]:
            assert entry["token"][:16] not in output


# --- T19: verification ----------------------------------------------------


def _transport(status: int = 200, body: dict[str, Any] | None = None) -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {"sId": 21, "mday": 2})

    return httpx.MockTransport(handler)


def _exploding_transport() -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("--no-verify still made a request")

    return httpx.MockTransport(handler)


def test_no_verify_stores_without_firing_a_request(stub_db: Any, with_key: None) -> None:
    result = login.run(report=console, 
        browser_factory=_FakeContext(),
        verify=False,
        transport=_exploding_transport(),
        prompt=_answers(""),
        now=NOW,
    )

    assert len(result.stored) == 2
    assert result.verified == []


def test_a_200_marks_each_lega_verified(stub_db: Any, with_key: None) -> None:
    result = login.run(report=console, 
        browser_factory=_FakeContext(),
        transport=_transport(),
        prompt=_answers(""),
        now=NOW,
    )

    assert sorted(result.verified) == sorted([_tokens.LEGA_CLASSIC, _tokens.LEGA_MANTRA])
    assert result.failures == []


def test_a_rejected_token_is_reported_but_the_row_stays_stored(
    stub_db: Any, with_key: None
) -> None:
    """last_verified_at is nullable precisely so a blip costs no credential."""
    result = login.run(report=console, 
        browser_factory=_FakeContext(),
        transport=_transport(401, {"code": "ATH001"}),
        prompt=_answers(""),
        now=NOW,
    )

    assert len(result.stored) == 2
    assert result.verified == []
    assert len(result.failures) == 2
    assert all("fantabot auth login" in reason for _, reason in result.failures)


def test_the_report_contains_no_token_and_no_key(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    login.run(report=console, 
        browser_factory=_FakeContext(),
        transport=_transport(),
        prompt=_answers(""),
        now=NOW,
    )
    output = capsys.readouterr().out

    assert GOOD_KEY[:8] not in output
    for entry in _tokens.storage_state()["origins"][0]["localStorage"]:
        if entry["name"] == "LEAGUES2024_LOCAL":
            blob = json.loads(entry["value"])
            for lega in blob[f"current-user-{_tokens.USER_ID}"]["leagues"]:
                assert lega["token"][:16] not in output


def test_every_printed_line_is_derivable_without_a_decrypt(
    stub_db: Any, with_key: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """lega id, name, team id, expiry — all plaintext columns or claims."""
    login.run(report=console, 
        browser_factory=_FakeContext(),
        transport=_transport(),
        prompt=_answers(""),
        now=NOW,
    )
    output = capsys.readouterr().out

    assert str(_tokens.TEAM_MANTRA) in output
    assert "2027-08-19" in output
    assert "200" in output


# --- T20: through the real command ----------------------------------------


def test_fantabot_auth_is_gone() -> None:
    from typer.testing import CliRunner

    from fantabot.interface.app import app

    result = CliRunner().invoke(app, ["auth"])

    assert result.exit_code != 0


def test_login_is_registered_with_all_four_flags() -> None:
    from typer.testing import CliRunner

    from fantabot.interface.app import app

    output = CliRunner().invoke(app, ["auth", "login", "--help"]).output

    for flag in ("--league", "--force", "--no-verify", "--save-session"):
        assert flag in output, f"{flag} is missing from `fantabot auth login --help`"


def test_a_missing_key_exits_two_through_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC 1, through the real Typer command rather than login.run(report=console, )."""
    from typer.testing import CliRunner

    from fantabot import config
    from fantabot.interface.app import app

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "")
    result = CliRunner().invoke(app, ["auth", "login"])

    assert result.exit_code == 2
    assert "Fernet.generate_key()" in result.output


def test_a_malformed_key_exits_two_through_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from fantabot import config
    from fantabot.interface.app import app

    monkeypatch.setattr(config.settings, "fantabot_encryption_key", "not-a-key")
    result = CliRunner().invoke(app, ["auth", "login"])

    assert result.exit_code == 2
    assert "44-character urlsafe-base64" in result.output


def test_login_navigates_to_the_site_root_not_to_lega_url(
    stub_db: Any, with_key: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed on a real run: `.env.example` ships LEGA_URL as the placeholder
    `.../nome-della-tua-lega`, which is non-empty — so an `or` fallback never
    fires and the browser lands on a dead page. Signing in does not need a
    lega-specific URL, and the root always works."""
    from fantabot import config

    monkeypatch.setattr(
        config.settings, "lega_url", "https://leghe.fantacalcio.it/nome-della-tua-lega"
    )

    class _Recording(_FakeContext):
        def __init__(self) -> None:
            super().__init__()
            self.urls: list[str] = []

        def new_page(self) -> Any:
            outer = self

            class _P(_FakePage):
                def goto(self, url: str) -> None:
                    outer.urls.append(url)
                    super().goto(url)

            self.page = _P()
            return self.page

    ctx = _Recording()
    login.run(report=console, browser_factory=ctx, verify=False, prompt=_answers(""), now=NOW)

    assert ctx.urls == [login.LOGIN_URL]
    assert "nome-della-tua-lega" not in ctx.urls[0]
