"""The golden harness: run the asta commands against pinned data, socket-free.

**What this is for.** Every later phase of the simplification deletes, renames, moves or
rewrites code these three commands run through. The one thing that may not change is what
they print. This module makes that checkable on every `pytest`, which is the only place it
is worth checking — a gate that runs on a tier nobody runs before committing is not a gate.

**Why it is fixture-driven rather than a `db`-marked capture.** `conftest.py` blocks
`socket.connect` for every non-`db` node and `pyproject.toml` sets `addopts = "-m 'not db'"`.
A golden that reads Postgres therefore either cannot run, or does not run at the moment it
matters. So the three reads the commands make are captured to `tests/golden/` (294 KB) and
served back through fakes. Everything downstream of those reads — the value model, the
sentiment gate and tilt, the legality matrix, the optimizer, the reservation walk-aways, the
opponent tracker and the rendering — is the real code.

**Why the clock is pinned.** `sentiment.py:153` decays confidence on a 7-day half-life
against `as_of`, and every stored row shares one `data_run`. One day of drift rescales every
reading: the same inputs print `obj 2273.1`, then `2209.1`, then `1936.5` a week later, with
roster *membership* changing too. `asta_engine.cli._today` is the single seam
(`tests/test_asta_clock.py` keeps it single) and it is frozen here.

**The rule, which is the whole point.** This harness may change *how* it reaches the code.
It may never change the *bytes* it asserts. A golden that goes red is a finding, not a
regeneration prompt — see `test_golden.py`'s update mode, which is built so it cannot pass.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import fields
from datetime import date
from typing import Any
from unittest.mock import patch

from _paths import GOLDEN

from fantabot.data_sources.models import QuotazioneRow, SentimentRow

GOLDEN = GOLDEN

#: The day the captured `player_sentiment` rows were produced. Freezing `_today` here makes
#: the confidence decay exactly zero, which is the only value that does not drift.
PINNED_TODAY = date(2026, 8, 28)

#: Rich caches width and colour in `Console.__init__`, and `cli.py` builds one at import.
#: `conftest.py` already sets these at module scope; they are restated as an explicit
#: precondition so a golden capture never silently depends on the ambient terminal.
CONSOLE_ENV = {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}


def _jsonl(name: str) -> Iterator[dict[str, Any]]:
    with (GOLDEN / name).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def load_quotazioni() -> dict[str, QuotazioneRow]:
    """The 548-player Mantra listone, in the repository's own stable id order."""
    out: dict[str, QuotazioneRow] = {}
    for row in _jsonl("quotazioni.jsonl"):
        out[row["player_id"]] = QuotazioneRow(
            player_id=row["player_id"],
            nome=row["nome"],
            squadra=row["squadra"],
            ruoli_codice=tuple(row["ruoli_codice"]),
            ruoli=tuple(row["ruoli"]),
            fvm=row["fvm"],
        )
    return out


def load_sentiment() -> dict[str, SentimentRow]:
    """One reading per player, all from `data_run` = PINNED_TODAY."""
    names = {f.name for f in fields(SentimentRow)}
    return {
        row["player_id"]: SentimentRow(**{k: v for k, v in row.items() if k in names})
        for row in _jsonl("sentiment.jsonl")
    }


def load_clearing_sales() -> list[tuple[str, int]]:
    """Every Mantra sale of our league shape, in the query's own `ORDER BY`."""
    with (GOLDEN / "clearing_sales.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [(row["player_id"], int(row["price"])) for row in reader]


class _FakeReferenceRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    def quotazioni(self, stagione: str, listone: str) -> dict[str, QuotazioneRow]:
        assert (stagione, listone) == ("2026/27", "mantra"), (
            f"the fixture is the 2026/27 Mantra listone; got {stagione}/{listone}"
        )
        return load_quotazioni()


class _FakeAsteRepository:
    def __init__(self, session: Any) -> None:
        self._session = session

    def mantra_clearing_sales(
        self, *, budget: int = 500, num_teams: int = 8
    ) -> list[tuple[str, int]]:
        assert (budget, num_teams) == (500, 8), (
            f"the fixture is the 8x500 league shape; got {num_teams}x{budget}"
        )
        return load_clearing_sales()


class _FakeSentimentSource:
    def __init__(self, session: Any) -> None:
        self._session = session

    def all_latest(self, *, data_run: date | None = None) -> dict[str, SentimentRow]:
        assert data_run in (None, PINNED_TODAY), (
            f"the fixture holds one run, {PINNED_TODAY}; got {data_run}"
        )
        return load_sentiment()


class _FakeDatabaseManager:
    """Hands out a sentinel. No fake repository ever touches it."""

    @contextmanager
    def get_session(self) -> Iterator[object]:
        yield object()


@contextmanager
def pinned_world(*, today: date | None = None) -> Iterator[None]:
    """The five patch points, plus the console environment. Nothing opens a socket.

    `today` defaults to the captured `data_run`, which makes the confidence decay exactly
    1.0 — the only value that cannot drift. One case deliberately passes a later date, so
    `half_life_days` is covered: at zero age `0.5 ** (0 / h)` is 1.0 for *every* half-life,
    so a golden pinned only at the run date cannot see that constant at all.
    """
    previous = {key: os.environ.get(key) for key in CONSOLE_ENV}
    os.environ.update(CONSOLE_ENV)
    try:
        with ExitStack() as stack:
            for target, replacement in (
                ("fantabot.adapters.persistence.database_manager", _FakeDatabaseManager()),
                ("fantabot.adapters.persistence.repositories.reference.ReferenceRepository", _FakeReferenceRepository),
                ("fantabot.adapters.persistence.repositories.aste.AsteRepository", _FakeAsteRepository),
                ("fantabot.data_sources.news_sentiment.NewsSentimentSource", _FakeSentimentSource),
                ("fantabot.interface.asta._today", lambda: today or PINNED_TODAY),
            ):
                stack.enter_context(patch(target, replacement))
            yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(argv: list[str], *, today: date | None = None) -> str:
    """Invoke the real CLI against the pinned world and return exactly what it printed.

    The exit code is folded into the captured text rather than asserted, because *a command
    that fails today* is part of what this pins: `asta live` with a team that actually owns
    players raises `InfeasibleRoster`, and that must show up as a deliberate golden change
    when it is fixed, not as a silent one.
    """
    from typer.testing import CliRunner

    from fantabot.cli import app

    with pinned_world(today=today):
        result = CliRunner().invoke(app, argv)

    body = result.output
    if result.exit_code != 0:
        failure = type(result.exception).__name__ if result.exception else "SystemExit"
        body = f"{body}\n[exit {result.exit_code}: {failure}]\n"
    return body
