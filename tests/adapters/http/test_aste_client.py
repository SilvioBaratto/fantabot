"""The live-auctions client: an authenticated GET, parsed into configs.

No socket opens — the transport is injected, as it is for the stream. What is
pinned is the handling of the two answers that are easy to get wrong: an empty
list, which looks exactly like a quiet night, and a 401, which looks like
nothing at all if it is swallowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fantabot import config
from fantabot.adapters.http.harvest.client import (
    BASE_URL_FIELD,
    BASE_URL_VAR,
    LIVE_PATH,
    AuthExpired,
    LiveAuctionsClient,
    ScanEmpty,
)

#: What an unset base must resolve to, written out rather than read back from `Settings`:
#: the point of these tests is that the scan hits the real site when nothing is configured,
#: and an assertion phrased as ``default == default`` cannot fail.
DECLARED_DEFAULT = "https://api.fantalab.it"


def _card(auction_id: str, asta_type: str = "mantra") -> dict[str, object]:
    return {
        "fantaleague_id": auction_id,
        "db": 15,
        "asta_type": asta_type,
        "fantaleague_name": "Lega",
        "num_teams": 8,
        "num_credits": 500,
        "asta_mode": "random",
        "raise_mode": "free",
    }


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _client(response: _Response, recorder: list | None = None) -> LiveAuctionsClient:
    def get(url: str, headers: dict[str, str]) -> _Response:
        if recorder is not None:
            recorder.append((url, headers))
        return response

    return LiveAuctionsClient(token="tok", get=get)


def test_every_format_is_returned_because_the_filter_is_omitted() -> None:
    """`asta_type` is an optional query parameter. Omitting it is how the scan
    stops throwing away 85% of the population — we play both formats."""
    recorder: list = []
    configs = _client(
        _Response(200, [_card("a", "mantra"), _card("b", "classic")]), recorder
    ).live_auctions()
    url, _ = recorder[0]
    assert "asta_type" not in url
    assert url.endswith(LIVE_PATH)
    assert {c.asta_type for c in configs} == {"mantra", "classic"}


def _scanned_url() -> str:
    """The URL one scan actually GETs."""
    recorder: list = []
    _client(_Response(200, [_card("a")]), recorder).live_auctions()
    return str(recorder[0][0])


def test_the_host_is_the_configured_base_not_a_hardcoded_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FANTABOT_FANTALAB_BASE_URL` is a knob whose whole purpose is to point the app
    somewhere else. It was obeyed by `rest.fetch_league` and ignored here, so the scan kept
    hitting the real site while everything else had moved.

    The assertion is on the **override**, never on the literal `api.fantalab.it`: that
    one passes against the bug.
    """
    monkeypatch.setenv(BASE_URL_VAR, "https://sentinel.invalid")
    assert _scanned_url() == "https://sentinel.invalid" + LIVE_PATH


def test_the_base_is_read_when_the_scan_runs_not_when_the_process_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The variable is exported *after* the process started — `fantabot.config` was
    imported at collection, long before this line — and the scan must still obey it.

    That is the whole of the distinction: reading `settings.fantabot_fantalab_base_url`
    per call is not reading it live, because the singleton behind it was built at first
    import and nothing re-reads the environment into it. A `fantabot_app` server that
    lives for days would answer every scan with the state of the world at boot, which is
    the failure `config.live_auto_act` exists to prevent. Against a singleton read this
    test fails; against `live_setting` it passes.
    """
    recorder: list = []
    client = _client(_Response(200, [_card("a")]), recorder)
    monkeypatch.setenv(BASE_URL_VAR, "https://later.invalid")
    client.live_auctions()
    assert recorder[0][0] == "https://later.invalid" + LIVE_PATH


def test_a_dotenv_edited_under_a_running_process_is_read_fresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half of `live_auto_act`'s precedence: with nothing exported, the `.env`
    is read from disk at scan time. This is the operator who repoints the host in the file
    and does not restart the server — and `.env` is resolved against the working directory,
    which is why this chdirs rather than trusting the repository's own.
    """
    monkeypatch.delenv(BASE_URL_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{BASE_URL_VAR}=https://from-dotenv.invalid\n")
    assert _scanned_url() == "https://from-dotenv.invalid" + LIVE_PATH


def test_an_exported_variable_outranks_the_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Root `CLAUDE.md`'s rule, on the one setting where getting it backwards points a
    scan at the wrong host: an export is the operator speaking later than the file.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{BASE_URL_VAR}=https://from-dotenv.invalid\n")
    monkeypatch.setenv(BASE_URL_VAR, "https://exported.invalid")
    assert _scanned_url() == "https://exported.invalid" + LIVE_PATH


def test_a_launcher_injected_base_loses_to_a_later_edit_of_that_same_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The app-server case, whole. The launcher copies `.env` into `os.environ` at boot and
    registers what it injected (`config.note_dotenv_injection`), so a value that *looks*
    exported is known to be the file speaking; the operator then edits that file and does
    not restart. The boot value must lose.

    Without the registration the two are indistinguishable in `os.environ` and the edit does
    nothing — the shape of the `FANTABOT_AUTO_ACT` incident that whole mechanism exists for.
    This is also the test that separates `live_setting` from a fresh `Settings()` per call:
    the latter is live too, but `os.environ` outranks its dotenv, so it would answer
    `at-boot.invalid` here.
    """
    env = tmp_path / ".env"
    env.write_text(f"{BASE_URL_VAR}=https://edited.invalid\n")
    monkeypatch.setenv(BASE_URL_VAR, "https://at-boot.invalid")  # what `load_dotenv` copied
    monkeypatch.setattr(config, "_DOTENV_INJECTED", {})  # an isolated registry, restored after
    config.note_dotenv_injection(env, [BASE_URL_VAR])
    assert _scanned_url() == "https://edited.invalid" + LIVE_PATH


@pytest.mark.parametrize("raw", ["", "   ", "/"])
def test_a_base_that_says_nothing_falls_back_to_the_declared_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """`live_setting` returns the raw string, so *unset* is this module's decision, and
    the only safe reading of a blank one is the default `config.py` declares.

    ``None`` would crash and an empty host is worse: it leaves the relative path
    `/fantaleagues/live`, which no reader of the resulting error would recognise as a
    configuration mistake. A bare `/` is in here because it survives `.strip()`.
    """
    monkeypatch.setenv(BASE_URL_VAR, raw)
    assert _scanned_url() == DECLARED_DEFAULT + LIVE_PATH


def test_nothing_configured_anywhere_is_the_real_site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither exported nor in any `.env` — the cron path, and the shape of every run
    before the variable existed. It must still scan `api.fantalab.it`.
    """
    monkeypatch.delenv(BASE_URL_VAR, raising=False)
    monkeypatch.chdir(tmp_path)  # a directory with no `.env` at all
    assert _scanned_url() == DECLARED_DEFAULT + LIVE_PATH


def test_the_declared_default_is_the_one_config_declares() -> None:
    """`DECLARED_DEFAULT` is written out above so the fallback assertions can fail. The
    coupling that buys is paid for here: change the host in `config.py` and this is what
    says so, rather than four tests quietly re-deriving whatever it became.

    It reads `.default`, which is what `_live_url` reads: a field switched to a
    `default_factory` leaves that `PydanticUndefined`, and this is where that surfaces
    instead of in a scan of ``PydanticUndefined/fantaleagues/live``.
    """
    assert config.Settings.model_fields[BASE_URL_FIELD].default == DECLARED_DEFAULT


def test_the_variable_read_is_the_one_pydantic_resolves_the_field_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`BASE_URL_VAR` is derived — no ``env_prefix``, so the field name uppercased — and
    that derivation is an assumption about pydantic-settings, not a fact about this module.
    Wrong, it would read a variable nobody sets while `Settings` read another, and every
    test above would still pass by agreeing with itself. So it is checked against pydantic.
    """
    monkeypatch.setenv(BASE_URL_VAR, "https://pinned.invalid")
    assert config.Settings().fantabot_fantalab_base_url == "https://pinned.invalid"


def test_a_trailing_slash_on_the_base_does_not_double_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator types the base with or without one; `//fantaleagues/live` is a 404."""
    monkeypatch.setenv(BASE_URL_VAR, "https://sentinel.invalid/")
    assert _scanned_url() == "https://sentinel.invalid" + LIVE_PATH


def test_the_token_travels_in_the_header_and_nowhere_else() -> None:
    recorder: list = []
    _client(_Response(200, [_card("a")]), recorder).live_auctions()
    url, headers = recorder[0]
    assert headers["Authorization"] == "Bearer tok"
    assert "tok" not in url, "a credential must never reach a URL"


def test_an_expired_session_is_named_not_swallowed() -> None:
    """A 401 means the id_token aged out — about an hour after capture. Silently
    returning nothing would look exactly like a night with no auctions."""
    with pytest.raises(AuthExpired, match="auth fantalab-login"):
        _client(_Response(401, {"error": "unauthorized"})).live_auctions()


def test_an_empty_list_is_refused_rather_than_returned() -> None:
    """Zero live auctions is possible at 5am and indistinguishable from a broken
    scan. The caller is told, and decides."""
    with pytest.raises(ScanEmpty):
        _client(_Response(200, [])).live_auctions()


def test_a_hostile_shard_is_refused_at_the_boundary() -> None:
    """The response is remote content. `db` lands in a hostname."""
    from fantabot.domain.harvest.models import ShardError

    bad = _card("a")
    bad["db"] = "evil.com#"
    with pytest.raises(ShardError):
        _client(_Response(200, [bad])).live_auctions()


def test_an_unexpected_status_is_reported_with_its_code() -> None:
    with pytest.raises(RuntimeError, match="503"):
        _client(_Response(503, {})).live_auctions()
