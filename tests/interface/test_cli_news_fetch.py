"""news fetch behaviours that need neither the database nor an agent.

The rest moved to tests/integration/: the pool is a query now, so the command
needs the stack up even for --no-run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import _importgraph as G
import pytest
from typer.testing import CliRunner

from fantabot.application.news_fetcher import FetchResult
from fantabot.domain.news.pool import PoolPlayer
from fantabot.interface.app import app

#: The persistence package, spelled once. Its name moves in W6.
PERSISTENCE = "fantabot.adapters.persistence"

runner = CliRunner()


def test_scope_roster_errors_instead_of_silently_fetching_the_whole_pool() -> None:
    # Falling back to `pool` would spend 523 queries for someone who asked for ~25,
    # and look like it worked.
    result = runner.invoke(app, ["news", "fetch", "--scope", "roster"])

    assert result.exit_code != 0
    output = result.output.lower()
    assert "roster" in output
    assert "api" in output  # names the league-API work as the blocker


def test_the_pipeline_never_writes() -> None:
    """CLAUDE.md's rule and nine tests depend on fetch_all returning a result
    rather than persisting one. Pushing inserts into it would make the fan-out
    untestable without a database.

    The module is located through the import system rather than by a path literal, so
    W6 changes its name here and nothing else. The database half is asked of the import
    graph; the other two stay text checks because they name *calls*, which an import
    graph cannot see, and because being blunt is the point -- P11-4 tried to move a read
    in here and this is what stopped it.
    """
    import fantabot.application.news_fetcher as pipeline

    source = Path(pipeline.__file__).read_text()

    assert not G.reaches(pipeline.__name__, PERSISTENCE)
    assert "upsert" not in source
    assert "append_rows" not in source


# -- the thirteen statements the db tier does not reach ------------------------------------
#
# `tests/integration/test_news_fetch_write.py` drives this command hard — 18 tests, the
# whole stack real below `fetch_all`. Measured across both tiers on 2026-09-24 the body is
# **99 of 112 statements**, not the 11% a default-tier run reports: that run deselects
# `-m db`, and a tier-scoped number read as a whole-repo one is how this command came to
# look like the largest coverage gap in the CLI when it is one of the best covered.
#
# What is left is seven behaviours that need no database, so they belong here rather than
# behind the `db` marker and its four minutes. Each is a branch the integration file has no
# reason to take: it always writes, always resolves a model, and always hands back a pool.

class _Session:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _player(pid: str, nome: str) -> PoolPlayer:
    return PoolPlayer(id=pid, nome=nome, squadra="Inter", ruolo="A", ruoli_mantra=("Pc",))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The pool, the resume filter and the fan-out, all faked. No socket, no agent.

    Every import in the command body is function-local, so these module attributes are
    read when it runs.
    """
    from fantabot.adapters.agent import env as agent_env
    from fantabot.adapters.persistence import database_manager, news_pool
    from fantabot.adapters.persistence.repositories import sentiment as sentiment_repo
    from fantabot.application import news_fetcher as pipeline
    from fantabot.config import settings

    state = SimpleNamespace(
        players=[_player("1", "Lautaro"), _player("2", "Thuram")],
        existing=set(),
        result=FetchResult(),
        on_fetch=None,
        stripped=[],
        queried=None,
    )

    monkeypatch.setattr(
        type(settings), "resolve_agent_model", lambda _self, _override="": "a-model"
    )
    monkeypatch.setattr(database_manager, "_session_factory", _Session)
    monkeypatch.setattr(news_pool, "load_pool", lambda _s, _season: list(state.players))
    monkeypatch.setattr(
        sentiment_repo,
        "SentimentRepository",
        lambda _s: SimpleNamespace(existing_keys=lambda _d: set(state.existing)),
    )
    monkeypatch.setattr(agent_env, "strip_dangerous_env", lambda: state.stripped.append(1))

    async def _fetch_all(players: Any, **kwargs: Any) -> FetchResult:
        state.queried = list(players)
        if state.on_fetch is not None:
            state.on_fetch(kwargs)
        return state.result

    monkeypatch.setattr(pipeline, "fetch_all", _fetch_all)
    return state


def test_an_unresolvable_model_exits_2_before_the_pool_is_read(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal sits above the first `get_session()` on purpose — a run that cannot
    name a model has nothing to ask the database."""
    from fantabot.adapters.persistence import news_pool
    from fantabot.config import settings

    def _boom(_self: object, _override: str = "") -> str:
        raise RuntimeError("no model: set FANTABOT_AGENT_MODEL or pass --model")

    monkeypatch.setattr(type(settings), "resolve_agent_model", _boom)
    monkeypatch.setattr(
        news_pool, "load_pool", lambda *_a: pytest.fail("the pool was read anyway")
    )

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 2
    assert "no model" in result.output
    assert wired.queried is None


def test_only_filters_the_pool_by_substring(wired: SimpleNamespace) -> None:
    """Case-insensitive, and applied before the resume filter counts candidates — so
    `--only` on a name already collected still reports the resume, not the whole pool."""
    result = runner.invoke(app, ["news", "fetch", "--only", "thur"])

    assert result.exit_code == 0
    assert [p.nome for p in wired.queried] == ["Thuram"]


def test_a_pool_already_collected_today_says_so_and_queries_nothing(
    wired: SimpleNamespace,
) -> None:
    """The resume filter emptying the list is a clean finish, not a failure: this is the
    state every re-run of a completed week lands in, and it must not spend a query or
    strip the environment for one."""
    today = date.today().isoformat()
    wired.existing = {(today, "1"), (today, "2")}

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 0
    assert "Nothing to do" in result.output
    assert wired.queried is None
    assert wired.stripped == []


def test_a_second_interrupt_restores_the_handler_and_stops_now(
    wired: SimpleNamespace,
) -> None:
    """First Ctrl-C asks the fan-out to stop; the second must hurt.

    The handler is called directly, never raised at the process — a test that signals its
    own runner is a test that can kill the suite. The second call restores whatever was
    installed before, so the third Ctrl-C is the interpreter's own.
    """
    import signal

    before = signal.getsignal(signal.SIGINT)
    seen: dict[str, Any] = {}

    def _two_interrupts(kwargs: dict[str, Any]) -> None:
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        seen["after_first"] = kwargs["should_stop"]()
        seen["still_ours"] = signal.getsignal(signal.SIGINT) is handler
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        seen["restored"] = signal.getsignal(signal.SIGINT) is before

    wired.on_fetch = _two_interrupts

    result = runner.invoke(app, ["news", "fetch"])

    assert seen["after_first"] is True, "the first Ctrl-C did not reach the fan-out"
    assert seen["still_ours"] is True, "the first Ctrl-C already restored the handler"
    assert seen["restored"] is True, "the second Ctrl-C left ours installed"
    assert signal.getsignal(signal.SIGINT) is before
    assert result.exit_code == 0


def test_a_run_off_the_main_thread_still_collects(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`signal.signal` raises ValueError outside the main thread. Only the graceful stop
    is unavailable there; refusing to collect over it would trade the whole run for a
    convenience, and the `finally` must not try to restore a handler never installed."""
    import signal

    def _refuse(*_a: Any, **_kw: Any) -> None:
        raise ValueError("signal only works in main thread")

    monkeypatch.setattr(signal, "signal", _refuse)

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 0
    assert wired.queried is not None, "the run refused to collect"


def test_rate_limits_are_reported_rather_than_hidden(wired: SimpleNamespace) -> None:
    """The run survived them, so it is not a failure — but a week collected through a
    backoff is worth knowing about before the numbers are trusted."""
    wired.result = FetchResult(rate_limited=True)

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 0
    assert "rate limits were hit" in result.output


def test_without_write_the_rows_are_printed_and_said_to_be_discarded(
    wired: SimpleNamespace,
) -> None:
    """The dry run's whole output. Printing the rows and *not* saying they were dropped
    is the reading that makes an operator think the week is collected."""
    row = {"id": "1", "data_run": "2026-09-24", "sentiment": "0.4", "confidenza": "0.8",
           "n_fonti": "3"}
    wired.result = FetchResult(rows=[row], failures=[("Thuram", "no sources")])

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 0
    # The rows themselves, not only the count: a summary line that says "1 rows
    # discarded" over no rows at all reads identically and shows the operator nothing.
    assert "sentiment" in result.output
    assert "0.4" in result.output
    assert "1 rows discarded" in result.output
    assert "--write not given" in result.output
    assert "1 failures" in result.output
    assert "no sources" in result.output


def test_a_dry_run_that_stopped_early_still_exits_non_zero(
    wired: SimpleNamespace,
) -> None:
    """`_report_stop` is reached on both branches. A backend that died halfway through a
    dry run is the same backend that would die through a real one, and cron has to hear
    it either way."""
    wired.result = FetchResult(
        rows=[], stopped_early="10 failures in a row — stopping", skipped=458
    )

    result = runner.invoke(app, ["news", "fetch"])

    assert result.exit_code == 1
    assert "10 failures in a row" in result.output
    assert "458 player(s) were not queried" in result.output
