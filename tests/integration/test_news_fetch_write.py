"""`fantabot news-fetch --write` end to end, with the agent call faked out.

Everything downstream of the query is real: the CLI, the repository, the upsert
and the database. Only ``fetch_all`` is replaced, because a genuine run spends
523 agent queries and CLAUDE.md's rule is that the suite makes none.

What this closes is criterion 11 — the same day twice inserts once, and --force
updates in place rather than appending a second row that the reader would keep.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from fantabot.cli import app
from fantabot.db import database_manager
from fantabot.news.pipeline import FetchResult

pytestmark = pytest.mark.db

runner = CliRunner()
TODAY = date.today().isoformat()

#: A player id far outside the real range, so nothing the weekly run collects
#: can ever share a key with this file's writes.
CANARY_ID = 9_000_000_001
CANARY_NAME = "CANARY — test fixture, not a real player"


def _row(player_id: str, riassunto: str) -> dict[str, str]:
    return {
        "data_run": TODAY,
        "giorni_lookback": "14",
        "stagione": "2026/27",
        "id": player_id,
        "nome": "Canary",
        "squadra": "ATA",
        "ruolo": "Difensore",
        "ruoli_mantra": "B;DS",
        "ruolo_campo": "B",
        "deriva_ruolo": "0.00",
        "sentiment": "0.10",
        "disponibilita": "1.00",
        "titolarita": "0.90",
        "mercato": "0.00",
        "forma": "0.10",
        "rigorista": "0.00",
        "piazzati": "0.00",
        "confidenza": "0.80",
        "riassunto": riassunto,
        "n_fonti": "1",
        "fonti": "https://a",
        "modello": "fake",
    }


@pytest.fixture
def canary_player() -> Any:
    """A synthetic player, created for the test and removed after it.

    This file drives the real CLI, so its writes go through
    ``database_manager.get_session()``, which **commits** — unlike the
    ``db_session`` fixture, whose savepoint rolls back. Every row it writes and
    every row it deletes is real.

    It used to borrow ``SELECT id FROM players ORDER BY id LIMIT 1``: player 3,
    Radunovic, who has eight ``quotazioni`` rows and is therefore in the weekly
    pool. So ``pytest -m db`` deleted that week's reading for a real player,
    twice per test, and CLAUDE.md's rule is that a past Wednesday cannot be
    regenerated. Running the suite was quietly regenerating one.

    A synthetic id has no ``quotazioni`` row, so ``load_pool`` never returns it
    and no real reading can share its key. The deletes are therefore keyed on
    the player alone — every row for that id belongs to this file.
    """
    with database_manager.get_session() as session:
        session.execute(
            text("INSERT INTO players (id, nome) VALUES (:i, :n) ON CONFLICT (id) DO NOTHING"),
            {"i": CANARY_ID, "n": CANARY_NAME},
        )
        session.execute(
            text("DELETE FROM player_sentiment WHERE player_id = :p"), {"p": CANARY_ID}
        )
    yield str(CANARY_ID)
    with database_manager.get_session() as session:
        session.execute(
            text("DELETE FROM player_sentiment WHERE player_id = :p"), {"p": CANARY_ID}
        )
        session.execute(text("DELETE FROM players WHERE id = :p"), {"p": CANARY_ID})


def _fake_fetch(rows: list[dict[str, str]]) -> Any:
    async def fetch_all(*args: object, **kwargs: object) -> FetchResult:
        return FetchResult(rows=rows)

    return fetch_all


def _stored(player_id: str) -> list[str]:
    with database_manager.get_session() as session:
        return list(
            session.execute(
                text(
                    "SELECT riassunto FROM player_sentiment "
                    "WHERE data_run = :d AND player_id = :p"
                ),
                {"d": TODAY, "p": int(player_id)},
            ).scalars()
        )


def test_the_same_day_twice_stores_one_row(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    from fantabot.news import pipeline

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "prima")]))
    assert runner.invoke(app, ["news-fetch", "--write", "--limit", "1"]).exit_code == 0

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "seconda")]))
    assert runner.invoke(app, ["news-fetch", "--write", "--limit", "1"]).exit_code == 0

    assert _stored(canary_player) == ["prima"]


def test_force_updates_in_place_rather_than_appending(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """Today --force skips the resume filter and append_rows has no dedup, so
    it writes a duplicate the reader then keeps. This is the fix."""
    from fantabot.news import pipeline

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "prima")]))
    runner.invoke(app, ["news-fetch", "--write", "--limit", "1"])

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "corretta")]))
    result = runner.invoke(app, ["news-fetch", "--write", "--force", "--limit", "1"])

    assert result.exit_code == 0
    assert _stored(canary_player) == ["corretta"]


def test_scope_pool_builds_the_pool_from_postgres() -> None:
    """Moved from the default tier: the pool is a query now, so news-fetch
    needs the stack up even for --no-run."""
    result = runner.invoke(app, ["news-fetch", "--scope", "pool", "--limit", "1", "--no-run"])

    assert result.exit_code == 0


def test_print_prompt_with_no_run_spends_nothing() -> None:
    result = runner.invoke(app, ["news-fetch", "--limit", "1", "--print-prompt", "--no-run"])

    assert result.exit_code == 0
    assert "GIOCATORE" in result.output
    assert "Fonti preferite" in result.output


def _player(player_id: str) -> Any:
    from fantabot.news.pool import PoolPlayer

    return PoolPlayer(
        id=player_id, nome="Canary", squadra="ATA", ruolo="Difensore", ruoli_mantra="B;DS"
    )


def _progress(player_id: str, row: dict[str, str] | None, failure: str | None) -> Any:
    from fantabot.news.pipeline import PlayerOutcome, Progress

    return Progress(
        done=1,
        total=1,
        outcome=PlayerOutcome(
            player=_player(player_id), row=row, failure=failure, rate_limited=False
        ),
    )


def test_a_crash_mid_run_still_stores_what_had_been_fetched(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """The whole point of storing as it goes.

    ``--flush-every 50`` keeps the reading in the sink's pending list, so only
    the drain on the way out can save it. Everything after ``asyncio.run`` — the
    final extend, the drain, the report — is skipped when a coroutine raises.
    """
    from fantabot.news import pipeline

    row = _row(canary_player, "salvata")

    async def crashing(players: object, **kwargs: Any) -> None:
        kwargs["on_result"](_progress(canary_player, row, None))
        raise RuntimeError("the run died before its summary")

    monkeypatch.setattr(pipeline, "fetch_all", crashing)
    result = runner.invoke(
        app, ["news-fetch", "--write", "--limit", "2", "--flush-every", "50"]
    )

    assert result.exit_code != 0
    assert _stored(canary_player) == ["salvata"], "the fetched reading was discarded"


def test_a_failure_full_of_brackets_does_not_take_the_run_with_it(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """`outcome.failure` is agent-written text. Rich reads `[/serie-a/news]` as a
    closing tag and raises MarkupError, which inside the gathered coroutine ends
    a run that may be hours old — and it eats the `[type=..., input_value=...]`
    tail of every pydantic rejection, which is the whole diagnostic."""
    from fantabot.news import pipeline

    row = _row(canary_player, "sopravvissuta")
    hostile = "structured output failed the schema: [/serie-a/news] [type=float_parsing]"

    async def with_hostile_text(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_result"](_progress(canary_player, None, hostile))
        kwargs["on_result"](_progress(canary_player, row, None))
        return FetchResult(rows=[row], failures=[("Canary", hostile)])

    monkeypatch.setattr(pipeline, "fetch_all", with_hostile_text)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert _stored(canary_player) == ["sopravvissuta"]
    assert "serie-a/news" in result.output, "the diagnostic was swallowed by the markup parser"
    assert "float_parsing" in result.output


def test_the_run_names_the_player_it_is_querying(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """`on_start` fires inside the semaphore slot, so the name appears when the
    query actually begins. Two hours of silence was the whole complaint."""
    from fantabot.news import pipeline

    async def announcing(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_start"](_player(canary_player))
        return FetchResult()

    monkeypatch.setattr(pipeline, "fetch_all", announcing)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "-> Canary" in result.output


def test_a_finished_player_reports_its_scores_and_the_running_stored_count(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """The counter must move on the line itself. `player_sentiment` staying at
    zero for the whole run was indistinguishable from a stalled one."""
    from fantabot.news import pipeline

    row = _row(canary_player, "vista")

    async def reporting(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_result"](_progress(canary_player, row, None))
        return FetchResult(rows=[row])

    monkeypatch.setattr(pipeline, "fetch_all", reporting)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "1", "--flush-every", "1"])

    assert result.exit_code == 0, result.output
    plain = result.output
    assert "1/1" in plain, "the position in the run is missing"
    assert "Canary" in plain
    assert "sentiment 0.10" in plain
    assert "1 stored" in plain, "the running count is what makes a stall visible"


def test_a_second_run_says_what_it_is_resuming_from(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """Recovery is re-running. The resume filter has always existed and, until
    readings were stored as they landed, never had anything to skip."""
    from fantabot.news import pipeline

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([_row(canary_player, "prima")]))
    assert runner.invoke(app, ["news-fetch", "--write", "--limit", "1"]).exit_code == 0

    monkeypatch.setattr(pipeline, "fetch_all", _fake_fetch([]))
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "1"])

    assert "resuming" in result.output
    assert "already stored" in result.output


def test_a_database_that_fails_mid_run_is_named_and_the_run_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """`aste-load` names an unreachable database the pass it happens. Here the
    only other signal is the stored count quietly ceasing to advance while the
    counter, the scores and the ETA all go on looking healthy — and a run that
    stored nothing must not exit 0 and report the week as collected."""
    from fantabot.db.repositories.sentiment import SentimentRepository
    from fantabot.news import pipeline

    row = _row(canary_player, "mai scritta")

    def refusing(self: object, rows: object, *, force: bool = False) -> int:
        raise RuntimeError("connection refused")

    async def reporting(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_result"](_progress(canary_player, row, None))
        return FetchResult(rows=[row])

    monkeypatch.setattr(pipeline, "fetch_all", reporting)
    monkeypatch.setattr(SentimentRepository, "upsert_rows", refusing)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "1", "--flush-every", "1"])

    assert result.exit_code == 1, result.output
    assert "storing failed" in result.output
    assert "RuntimeError" in result.output
    assert "could not be stored" in result.output
    assert _stored(canary_player) == []



def test_the_canary_is_not_a_player_the_weekly_run_collects(canary_player: str) -> None:
    """This file drives the real CLI, so everything it writes and deletes is real.

    Unlike `db_session`, whose savepoint rolls back, these tests go through
    `database_manager.get_session()`, which commits. The canary fixture DELETEs
    `(today, canary)` at setup *and* teardown — so whoever the canary is, every
    `pytest -m db` erases that player's reading for the current week.

    It used to be `SELECT id FROM players ORDER BY id LIMIT 1`: player 3,
    Radunovic, who has eight `quotazioni` rows and is therefore in the weekly
    pool. CLAUDE.md's rule is that a past Wednesday cannot be regenerated, and
    running the suite was quietly regenerating one.

    A synthetic id has no `quotazioni` row, so `load_pool` never returns it and
    no real reading can ever share its key.
    """
    from sqlalchemy import text

    from fantabot.db import database_manager

    with database_manager.get_session() as session:
        in_pool = session.execute(
            text("SELECT count(*) FROM quotazioni WHERE player_id = :p"),
            {"p": int(canary_player)},
        ).scalar()
        nome = session.execute(
            text("SELECT nome FROM players WHERE id = :p"), {"p": int(canary_player)}
        ).scalar()

    assert in_pool == 0, (
        f"player {canary_player} has {in_pool} quotazioni row(s), so the weekly run collects "
        "them — and this file deletes their reading for today, twice per test"
    )
    assert nome == CANARY_NAME, "the canary must be recognisable as a fixture, not borrowed"


def test_the_fixture_does_not_borrow_a_real_player() -> None:
    """A tripwire on the exact shape of the regression, in the style of
    `test_db_boundary` and `test_token_secrecy`: the canary was picked by
    querying the real table, which is what made it a real player.

    Docstrings are excluded, or this test would fail on the paragraph above
    explaining what it forbids — a guard that cannot describe itself is a guard
    someone deletes.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                documentation.add(id(first.value))

    executed = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    ]
    # Split, so the needle is not itself one of the strings it is looking for.
    needle = "FROM players " + "ORDER BY"
    offenders = [sql for sql in executed if needle in sql]

    assert offenders == [], (
        f"the canary is being borrowed from the real players table again: {offenders}"
    )


def test_a_backend_failing_everything_stops_the_run_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """The 2026-08-28 shape: an exhausted Ollama quota returned HTTP 429 for every
    query from player 76 on, each arriving as the ordinary reason a single
    confused player produces. The run absorbed all of them and was on course to
    spend 458 more.

    The order matters as much as the exit code: the readings that did succeed
    are the expensive part, and they must be stored before the command gives up.
    """
    from fantabot.news import pipeline

    row = _row(canary_player, "prima del muro")
    reason = "agent returned no structured output"

    async def walled(players: object, **kwargs: Any) -> FetchResult:
        kwargs["on_result"](_progress(canary_player, row, None))
        return FetchResult(
            rows=[row],
            failures=[(f"P{i}", reason) for i in range(10)],
            stopped_early=(
                f"stopped after 10 consecutive failures with no success between them, "
                f"the last: {reason}"
            ),
            skipped=458,
        )

    monkeypatch.setattr(pipeline, "fetch_all", walled)
    result = runner.invoke(
        app, ["news-fetch", "--write", "--limit", "1", "--flush-every", "1"]
    )

    assert result.exit_code == 1, result.output
    assert "10 consecutive failures" in result.output
    assert "458 player(s)" in result.output
    assert "backend problem" in result.output, "it must not read as 458 bad players"
    assert _stored(canary_player) == ["prima del muro"], (
        "the run gave up before storing what it had already paid for"
    )


def test_the_run_day_can_be_pinned_so_a_resume_lands_on_the_same_week(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """`date.today()` is re-evaluated per invocation and feeds both halves of
    resume: the filter `existing_keys(today)` and the stored key `data_run`.

    A run of this command takes hours — measured at 1-2 players a minute over a
    548-player pool — so an evening start crosses midnight, and so does waiting
    out a backend outage. Today that silently begins a new week: the readings
    already collected stop being skipped, and 548 queries are spent again.
    Observed on 2026-08-28, when an exhausted quota stopped a run at player 90
    with 70 stored and the obvious recovery was to wait for the reset.
    """
    from datetime import date

    from fantabot.news import pipeline

    seen: dict[str, object] = {}

    async def capturing(players: object, **kwargs: Any) -> FetchResult:
        seen["today"] = kwargs["today"]
        return FetchResult()

    monkeypatch.setattr(pipeline, "fetch_all", capturing)
    result = runner.invoke(
        app, ["news-fetch", "--write", "--limit", "1", "--date", "2026-08-27"]
    )

    assert result.exit_code == 0, result.output
    assert seen["today"] == date(2026, 8, 27), "the pinned day never reached the fan-out"


def _refuses(monkeypatch: pytest.MonkeyPatch, day: str) -> Any:
    """Invoke with a bad --date, having made the fan-out unspendable.

    The fake is not decoration. Without it these two tests assert only the exit
    code, and the first regression of the guard sends them at the real backend:
    the check ran unfaked once during a mutation and queried a live model until
    it was killed. CLAUDE.md's rule is that the suite spends nothing, and a test
    that holds only while the code is correct does not keep it.
    """
    from fantabot.news import pipeline

    called: list[object] = []

    async def must_not_run(players: object, **kwargs: Any) -> FetchResult:
        called.append(players)
        return FetchResult()

    monkeypatch.setattr(pipeline, "fetch_all", must_not_run)
    result = runner.invoke(app, ["news-fetch", "--write", "--limit", "1", "--date", day])
    return result, called


def test_a_day_that_has_not_happened_is_refused(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """A typo in the year would write a phantom week that nothing collected,
    and the reader takes the most recent row per player."""
    result, called = _refuses(monkeypatch, "2027-08-28")

    assert result.exit_code == 2
    assert "2027-08-28" in result.output
    assert called == [], "refused, but only after the queries were spent"


def test_a_malformed_day_is_refused_rather_than_falling_back_to_today(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """Falling back would spend the queries under a key the operator did not ask
    for, which is the whole failure this option exists to prevent."""
    result, called = _refuses(monkeypatch, "27-08-2026")

    assert result.exit_code == 2
    assert "27-08-2026" in result.output
    assert called == []


def test_an_interrupt_stops_the_run_and_stores_what_it_had(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """Ctrl-C was ignored on 2026-08-28 — twice, thirty seconds apart. The
    escalation to SIGTERM skipped the drain written for interrupts, and four
    fetched readings queued in the sink went with it.

    The handler is called directly rather than raised at the process: that is
    exactly what Ctrl-C delivers, and a test that signals its own runner is a
    test that can kill the suite.
    """
    import signal

    from fantabot.news import pipeline

    row = _row(canary_player, "salvata dall'interrupt")
    seen: dict[str, object] = {}

    async def interrupting(players: object, **kwargs: Any) -> FetchResult:
        handler = signal.getsignal(signal.SIGINT)
        seen["installed"] = handler not in (signal.SIG_DFL, signal.default_int_handler)
        assert callable(handler), "no handler to exercise"
        should_stop = kwargs["should_stop"]
        seen["before"] = should_stop()
        handler(signal.SIGINT, None)
        seen["after"] = should_stop()
        return FetchResult(
            rows=[row],
            stopped_early="interrupted — no further player was queried",
            skipped=5,
        )

    before = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(pipeline, "fetch_all", interrupting)
    result = runner.invoke(
        app, ["news-fetch", "--write", "--limit", "1", "--flush-every", "1"]
    )

    assert seen["installed"] is True, "the command never installed an interrupt handler"
    assert seen["before"] is False
    assert seen["after"] is True, "Ctrl-C did not reach the fan-out"
    assert signal.getsignal(signal.SIGINT) is before, "the handler was left installed"
    assert _stored(canary_player) == ["salvata dall'interrupt"], (
        "the interrupt discarded a reading that had already been fetched"
    )
    assert result.exit_code == 1
    assert "interrupted" in result.output


def test_the_command_forwards_its_options_to_the_fan_out(
    monkeypatch: pytest.MonkeyPatch, canary_player: str
) -> None:
    """An option the command accepts and never passes on is worse than no option.

    `--max-consecutive-failures` shipped that way: the edit that was meant to add
    it to the `fetch_all` call matched nothing and said nothing, and the test for
    the breaker faked `fetch_all` to *return* a stopped run, so it never
    exercised the wiring. The breaker still worked at its default, which is why
    nothing looked wrong — the flag was simply inert, and `0` could not turn it
    off.
    """
    from fantabot.news import pipeline

    seen: dict[str, Any] = {}

    async def capturing(players: object, **kwargs: Any) -> FetchResult:
        seen.update(kwargs)
        return FetchResult()

    monkeypatch.setattr(pipeline, "fetch_all", capturing)
    result = runner.invoke(
        app,
        [
            "news-fetch", "--write", "--limit", "1",
            "--concurrency", "3",
            "--max-consecutive-failures", "7",
            "--lookback-days", "21",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["concurrency"] == 3
    assert seen["max_consecutive_failures"] == 7, "the flag never reached the fan-out"
    assert seen["lookback_days"] == 21
    assert callable(seen["should_stop"])
