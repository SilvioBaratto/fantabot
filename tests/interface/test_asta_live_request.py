"""What `asta live` asks for, as opposed to what it prints.

3.10 lifted the fold into `application/asta_advisory`; what stayed in the Typer body is the
translation from options to an `AdvisoryRequest`, and that is exactly the seam
`CLAUDE.md` records `GET /asta/plan` losing ten inputs at. The goldens cover the *output* of
one invocation with default options — they cannot see an option that stopped being
forwarded, because the golden was recorded without it.

Survivor 12 of 3.10's own battery is the case: replacing `sentiment=sentiment` with
`sentiment=True` left all 2,133 tests green. `--no-sentiment` is the ablation control — the
one switch whose whole purpose is to make the answer different — and nothing noticed it
being ignored.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *args: str
) -> dict[str, Any]:
    """Drive `asta live --replay` with the fold faked, and return the request it built.

    A real (empty) replay file, because `Path(replay).read_text()` is the Typer body's own
    and deliberately not faked: patching it would leave the one line that turns an operator's
    path into an argument untested.
    """
    import contextlib

    from fantabot.adapters.http.fantalab import listone
    from fantabot.adapters.persistence import database_manager
    from fantabot.interface import asta
    from fantabot.interface.app import app

    seen: dict[str, Any] = {}

    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 7})
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(asta, "parse_replay_lines", lambda _lines: [])
    monkeypatch.setattr(asta, "normalize", lambda _rows: [])

    def fake_build(_session: Any, request: Any, **kwargs: Any) -> Any:
        seen["request"] = request
        seen["bridge"] = kwargs["bridge"]

        class _Advisory:
            dropped_sales = 0
            result = None

        return _Advisory()

    import fantabot.application.asta_advisory as advisory

    monkeypatch.setattr(advisory, "build_advisory", fake_build)

    replay = tmp_path / "recorded.jsonl"
    replay.write_text("", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["asta", "live", "--replay", str(replay), "--team", "US", *args]
    )
    seen["result"] = result
    return seen


def test_the_ablation_control_reaches_the_fold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--no-sentiment` is the one switch whose whole purpose is a different answer, and
    the goldens cannot see it: they were recorded without it."""
    seen = _run(monkeypatch, tmp_path, "--no-sentiment")

    assert seen["result"].exit_code == 0, seen["result"].output
    assert seen["request"].sentiment is False


def test_sentiment_is_on_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: a body hard-coding `False` would pass the test above alone."""
    assert _run(monkeypatch, tmp_path)["request"].sentiment is True


def test_the_format_reaches_the_request_and_is_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--format` selects **both** the pool and the corpus, so a Classic replay read as
    Mantra is advised off players it cannot call, priced off another game — and nothing
    raises. The goldens cannot see it: they record one invocation, at the default.

    Both ways round, because a body that hardcoded `"classic"` would pass the first
    assertion alone — which is how `sentiment=True` shipped.
    """
    assert _run(monkeypatch, tmp_path, "--format", "classic")["request"].listone == "classic"
    assert _run(monkeypatch, tmp_path)["request"].listone == "mantra"


def test_an_unknown_format_is_refused_at_the_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A typo must not silently become the default. `asta bid` refuses the same way."""
    result = _run(monkeypatch, tmp_path, "--format", "mantraa")

    assert result["result"].exit_code != 0
    assert "mantra" in result["result"].output


def test_the_pinned_run_is_parsed_and_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A date, never a string. Parsing raises `typer.BadParameter`, which cannot happen in
    `application/` — the translation is what stays in the Typer body."""
    seen = _run(monkeypatch, tmp_path, "--sentiment-run", "2026-08-28")

    assert seen["request"].sentiment_run == date(2026, 8, 28)


def test_every_number_the_operator_chose_reaches_the_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ten-input drift, as a test: a field that stops being forwarded fails here.

    Deliberately none of these is a default — a request built from the command's own
    fallbacks would satisfy an assertion written against them.
    """
    seen = _run(
        monkeypatch,
        tmp_path,
        "--budget", "650", "--lam", "0.9", "--tilt-k", "0.4",
        "--teams", "10", "--credits", "650", "--season", "2025/26",
    )
    request = seen["request"]

    assert request.our_team_id == "US"
    assert (request.budget, request.lam, request.tilt_k) == (650.0, 0.9, 0.4)
    assert (request.num_teams, request.num_credits) == (10, 650)
    assert request.season == "2025/26"


def test_the_bridge_is_fetched_here_because_the_two_event_sources_differ(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--replay` is developer machinery and CLI-only (`tasks/archive/parity-spec.md` T20), so `build_advisory`
    takes events rather than reading them — one fold over two sources. The bridge is the
    same either way and is handed over."""
    assert _run(monkeypatch, tmp_path)["bridge"] == {"uuid-1": 7}


# -- `--league`: the format is detected, not defaulted ---------------------------------
#
# The replay path above keeps its stated `mantra` default and every assertion there stays
# green — a replay carries no format (`adapters/files/landing.py` writes `{seen_at,
# auction_id, state}` and the state is the `auction/<fl>` node, which has no such field),
# so a stated default is the honest answer. `--league` names a live room that *does* know,
# and defaulting there is what this section is about.


class TestTheDeclaredFormatProbe:
    """`_declared_format` — the authenticated rung, and the one that must never be fatal.

    It is a *seam*, taking `_fetch`, for the reason `_callable_ids` is one: a test that
    reached the real path would need a database session and a FantaLab bearer, and one that
    fakes the session instead passes because the fake has no `.load`, not because the probe
    degraded. That is a test passing for the wrong reason, which this repo has a written
    record of shipping.
    """

    @staticmethod
    def _probe(**kw: object) -> object:
        from fantabot.interface.asta import _declared_format

        return _declared_format("123", **kw)  # type: ignore[arg-type]

    def test_it_returns_what_the_room_declares(self) -> None:
        from types import SimpleNamespace

        assert self._probe(_fetch=lambda _id: SimpleNamespace(asta_type="classic")) == "classic"

    def test_a_room_that_declares_nothing_is_not_an_error(self) -> None:
        from types import SimpleNamespace

        assert self._probe(_fetch=lambda _id: SimpleNamespace(asta_type=None)) is None

    def test_a_missing_fantalab_session_degrades_rather_than_raising(self) -> None:
        """The whole point. `asta live --league` reads an **unauthenticated** ledger; a
        format probe that hard-failed without a bearer would turn a tokenless command into
        one that needs a login, which is a regression dressed as a fix."""
        from fantabot.domain.tokens.errors import FantalabSessionMissing

        def _no_session(_id: str) -> object:
            raise FantalabSessionMissing()

        assert self._probe(_fetch=_no_session) is None

    def test_a_missing_encryption_key_degrades_too(self) -> None:
        """`TokenCipher.__init__` raises before any fetch, so the guard has to enclose the
        cipher and not only the call. `asta room` builds its cipher outside the try; copying
        that shape here would crash a machine that has no key at all."""
        from fantabot.domain.tokens.errors import KeyMissing

        def _no_key(_id: str) -> object:
            raise KeyMissing()

        assert self._probe(_fetch=_no_key) is None

    def test_an_unreachable_fantalab_degrades_too(self) -> None:
        import httpx

        def _down(_id: str) -> object:
            raise httpx.ConnectError("no route")

        assert self._probe(_fetch=_down) is None

    def test_it_does_not_swallow_a_programming_error(self) -> None:
        """`except AttributeError` was in the first draft and would have made the two tests
        above pass against a fake session rather than against a degraded probe."""

        def _bug(_id: str) -> object:
            raise AttributeError("typo")

        with pytest.raises(AttributeError):
            self._probe(_fetch=_bug)

    def test_it_says_which_rung_failed(self) -> None:
        """"No FantaLab session" and "FantaLab is unreachable" send an operator to different
        fixes, and the rung below answers for a different population of rooms."""
        from fantabot.domain.tokens.errors import FantalabSessionMissing
        from fantabot.interface.asta import _declared_format

        said: list[str] = []

        def _no_session(_id: str) -> object:
            raise FantalabSessionMissing()

        assert _declared_format("123", warn=said.append, _fetch=_no_session) is None
        assert said and "format" in said[0]

    def test_a_silent_probe_is_the_default(self) -> None:
        """`warn` defaults to a sink, so a caller that has nowhere to print is not forced
        to invent one — and no module-level state carries a reason between runs."""
        from fantabot.domain.tokens.errors import FantalabSessionMissing

        def _no_session(_id: str) -> object:
            raise FantalabSessionMissing()

        assert self._probe(_fetch=_no_session) is None


class TestTheLeagueFormatIsDetected:
    """`asta live --league` took `--format`'s `mantra` default and never asked the room.

    `asta room` has read the room's own `asta_type` since `8cbd3ba` and refuses one that
    declares none; the advisory page does the same (`asta.ts` will not request an advisory
    until the room check produced a format, because "an advisory priced against another
    lega's game is worse than none"). This command was the one advisory surface still
    guessing — and the guess is invisible: exit 0, a full advisory, the wrong game.
    """

    @staticmethod
    def _run(
        monkeypatch: pytest.MonkeyPatch,
        *args: str,
        declared: str | None = None,
        recorded: str | None = None,
    ) -> dict[str, Any]:
        """Drive `--league` with both probes faked. Opens no socket and no database."""
        import contextlib

        import fantabot.interface.asta as cli
        from fantabot.adapters.http.fantalab import listone
        from fantabot.interface.app import app

        seen: dict[str, Any] = {}

        monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 7})
        monkeypatch.setattr(cli, "normalize", lambda _rows: [])

        def fake_build(_session: object, request: Any, **_kw: Any) -> Any:
            seen["request"] = request
            raise typer.Exit(0)

        import fantabot.application.asta_advisory as advisory

        monkeypatch.setattr(advisory, "build_advisory", fake_build)
        monkeypatch.setattr(cli, "_declared_format", lambda _id, **_kw: declared)
        monkeypatch.setattr(cli, "_recorded_format", lambda _id: recorded)
        monkeypatch.setattr(
            "fantabot.adapters.http.fantalab.feed.ledger_events", lambda _db, _lg: iter(())
        )
        monkeypatch.setattr(
            "fantabot.adapters.persistence.database_manager.get_session",
            lambda: contextlib.nullcontext(object()),
        )

        seen["result"] = CliRunner().invoke(
            app, ["asta", "live", "--league", "999", "--db", "3", "--team", "US", *args]
        )
        return seen

    def test_the_room_decides_the_listone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._run(monkeypatch, declared="classic")

        assert seen["request"].listone == "classic", seen["result"].output

    def test_the_corpus_answers_when_the_room_will_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._run(monkeypatch, declared=None, recorded="classic")

        assert seen["request"].listone == "classic", seen["result"].output

    def test_what_the_operator_typed_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._run(monkeypatch, "--format", "mantra", declared="classic")

        assert seen["request"].listone == "mantra"
        assert "--format" in seen["result"].output

    def test_nothing_known_is_refused_rather_than_priced_as_mantra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect itself. This used to be exit 0 and a Mantra-priced advisory."""
        seen = self._run(monkeypatch, declared=None, recorded=None)

        assert seen["result"].exit_code != 0
        assert "--format" in seen["result"].output
        assert "request" not in seen, "an advisory was built for a game nobody named"

    def test_the_provenance_is_printed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A format the room just stated and one harvested weeks ago are different facts."""
        seen = self._run(monkeypatch, declared=None, recorded="classic")

        assert "corpus" in seen["result"].output.lower()
