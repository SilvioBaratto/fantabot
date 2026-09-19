"""The parity tier: the CLI and the app, in one process, against one seeded database.

**Why a tier and not a handful of asserts.** A missing screen is a gap the operator can
see; a screen answering a *different question* is one they cannot. `GET /asta/plan` and
`asta optimize` differ in ten inputs today — `sentiment` is `None` on the page, which is
the ablation control of an experiment being shown to an operator as advice, and `tilt_k`
is 1.0 against the CLI's 0.25. Nothing anywhere compared the two, so nothing could say so.

Four decisions this file makes, each of which has a wrong version that looks fine:

* **The CLI runs in-process, through `typer.testing.CliRunner`.** Same interpreter, same
  `database_manager`, same `settings` singleton, and a real traceback when it fails. A
  subprocess would get its own `Settings()` from whatever `.env` the working directory
  happens to hold — the two-databases incident, reproduced inside the test that exists to
  prove there is one database.

* **The session comes from `database_manager`, never from a new engine.** That is what
  lets 0.7's exemption stay dropped (`test_api_holds_no_second_sqlalchemy_engine` scans
  `api/tests/` now), and it is also the only way the two sides can see the same rows: the
  CLI and the endpoint each open their own session, so a rolled-back outer transaction on
  a third connection is invisible to both. **The seed is therefore committed and swept**,
  which is safe precisely because this tier refuses to run against anything but its own
  database.

* **`fantabot_test`, refused by name.** The same guard `tests/conftest.py` applies to the
  `db` tier, restated here rather than imported: two test trees in two virtualenvs, and
  the CLI's `tests/` is not an importable package from the app's venv. A false refusal
  costs one environment variable; a false pass costs a corpus that cannot be regenerated.

* **The clock is frozen at both seams.** `interface/asta._today` and
  `interface/lineup._now` — one per surface, which `tests/domain/asta/test_asta_clock.py`
  is what keeps true. `sentiment.py` decays confidence on a 7-day half-life against
  `as_of`, so an unfrozen comparison is a coin flip that fails on the day the two sides
  are run either side of midnight.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from typer.testing import CliRunner, Result

from fantabot_app.api.main import app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark everything in this directory `parity`, so a new file cannot forget to.

    A module-level `pytestmark` in a conftest does nothing — it is collected from test
    *modules* only — and a file that forgets the marker does not fail loudly: it joins the
    default tier, where it opens a socket and fails for a reason that reads like a flake.
    """
    here = Path(__file__).parent
    for item in items:
        if Path(str(item.fspath)).parent == here:
            item.add_marker(pytest.mark.parity)

#: Synthetic ids, far above any real `players.id`, on the CLI tier's own convention
#: (`tests/conftest.py::SYNTHETIC_PLAYER_BASE`). Borrowing real players is how `pytest -m
#: db` came to be deleting a real player's weekly reading.
SYNTHETIC_BASE = 9_200_000_000

#: The season the seed is written under. Not the live one: a parity run must not be able
#: to read, or be read by, rows anybody cares about.
PARITY_SEASON = "1999/00"

#: The lega the seed describes. Neither of the operator's two (3584692, 4103937).
PARITY_LEAGUE = 999_000_001

#: The date every seam is frozen at. Fixed rather than `date.today()`, because a
#: reproducibility harness that reads the clock is the thing it exists to prevent.
FROZEN_TODAY = date(2026, 9, 1)


def _refuse_canonical(test_url: str, canonical_url: str) -> str:
    """`test_url`, unless it names the same database as `canonical_url`.

    By database **name alone** — a `fantabot` on another host is refused too. The CLI's
    `tests/conftest.py::refuse_canonical` is the same check for the same reason; it is
    restated rather than imported because `tests/` is not a package and this venv cannot
    reach it.
    """
    from sqlalchemy.engine import make_url

    if make_url(test_url).database == make_url(canonical_url).database:
        pytest.fail(
            "the parity tier would write to the canonical database "
            f"{make_url(test_url).database!r}. Make a separate one and point it there:\n"
            "  fantabot-app db create fantabot_test\n"
            '  FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_test)" '
            "alembic upgrade head\n"
            '  FANTABOT_TEST_DATABASE_URL="$(fantabot-app db url --database fantabot_test)" '
            "uv run pytest -m parity",
            pytrace=False,
        )
    return test_url


@pytest.fixture(scope="session")
def parity_dsn() -> str:
    """The tier's own database, refused if it is the canonical one."""
    from fantabot.config import Settings

    fresh = Settings()
    return _refuse_canonical(fresh.fantabot_test_database_url, fresh.fantabot_database_url)


@pytest.fixture
def parity_db(parity_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the *one* `database_manager` at the tier's database, for one test.

    `settings` is a module singleton read inside `DatabaseManager._factory`, so the
    environment variable alone is not enough — the singleton was built at import. The
    manager is disposed on both sides of the test so neither the tier nor whatever runs
    next inherits a pool bound to the wrong DSN.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.config import settings

    monkeypatch.setattr(settings, "fantabot_database_url", parity_dsn, raising=False)
    monkeypatch.setenv("FANTABOT_DATABASE_URL", parity_dsn)
    database_manager.dispose()

    try:
        with database_manager.get_session() as probe:
            probe.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        database_manager.dispose()
        pytest.skip(
            "the parity tier needs a migrated database — "
            "`fantabot-app db start` and `alembic upgrade head` against fantabot_test "
            f"({type(exc).__name__}: {str(exc).splitlines()[0]})"
        )

    yield
    database_manager.dispose()


@dataclass(frozen=True)
class SeededWorld:
    """What the seed put on disk, so a test asserts against it rather than a literal."""

    season: str
    league_id: int
    listone: str
    #: Player ids as the pool holds them — strings, because every consumer does.
    player_ids: tuple[str, ...]
    budget: int
    num_teams: int
    roster_size: int
    min_roles: tuple[int, ...]
    #: How many teams the **newest** capture holds. The earlier one holds two more, so a
    #: reader that ignores `captured_at` sees three.
    teams_in_latest_capture: int = 1
    #: The macro roles the pricing corpus fits a fade for. `GK` is deliberately absent —
    #: `training_pairs` drops keepers, so a seed cannot produce one.
    pricing_roles: tuple[str, ...] = ()
    #: Observations behind each of those fades. Carried because `fit_fades` drops a role
    #: below `MIN_OBSERVATIONS` without saying so, and a test asserting on the seed's own
    #: number catches the seed shrinking.
    pricing_observations: int = 0
    #: How many players the `TARGET_SEASON` universe holds, per listone. A floor on what
    #: a `run()` must store: below it the seed has stopped pricing some branch.
    pricing_universe: int = 0
    #: The `price_universe` flags the seed deliberately produces — its four non-fade
    #: branches. Declared here so a test compares the report to the seed's intent rather
    #: than to a literal list that drifts away from it.
    pricing_flags: tuple[str, ...] = ()


#: Named once so the seed and the sweep cannot disagree about what to delete.
_AUCTION_ID = "parity-auction"
#: `league_snapshot.captured_at` is `DateTime(timezone=True)`, so this is aware.
_CAPTURED_AT = datetime(1999, 1, 1, tzinfo=UTC)
#: A **second, earlier** capture, with a different team count.
#:
#: Without it "both surfaces read the same capture" is untestable: with one snapshot at one
#: `captured_at`, every filter selects the same row and none can be wrong. Proved by
#: mutation — dropping `latest_rosters`' `captured_at` filter left all five parity tests
#: green. The earlier capture holds *two* teams, so a reader that ignores the filter sees
#: three rows where the newest capture has one.
_EARLIER_AT = datetime(1998, 6, 1, tzinfo=UTC)

#: Eighteen players over three clubs, of which the lega's rosa holds twelve — so the
#: optimizer *chooses*, and a difference in the value model shows up as a difference in
#: membership.
#:
#: **The first version of this seed had eighteen roles and eighteen slots, and it made
#: the parity test pass.** With `roster_size == len(pool)` the plan is forced: every
#: candidate is bought whatever it is worth, so the two sides agreed about a decision
#: neither of them made. The same applies to the sentiment column below — the first
#: version gave every player the same scores, and `sentiment.py` normalises the pool mean
#: to exactly 1.0, so identical readings are arithmetically identical to no readings at
#: all. A fixture that cannot express the defect is a test that reports its absence.
#:
#: Two players are therefore *injured stars*: the highest `fvm` in the pool with a
#: `disponibilita` near zero. That is the 2026-08-28 shape the sentiment gate exists for —
#: a player with a metatarsal fracture reported by three sources, whom plain `fvm` chases
#: to 62 credits.
#:
#: Small on purpose otherwise: the tier is about whether two callers agree, not about
#: whether the optimizer is right — `tests/domain/asta/` is where that lives, and a
#: 570-player seed would make every parity failure a slow one. The roles cover 3-4-3's ten
#: outfield slots (`Dc Dc Dc E M C E W A Pc`) plus a keeper, with spares; eight players
#: was the first attempt and the optimizer refused it outright — Mantra legality is a
#: bipartite matching over eleven slots, so a rosa that cannot field an XI is not a small
#: plan, it is no plan.
#:
#: The three clubs are not decoration either: `lam` diversifies across clubs, so a
#: single-club pool makes that term inert and hides any divergence in it.
#:
#: `(nome, squadra, ruolo, fvm, disponibilita, titolarita)`
_POOL: tuple[tuple[str, str, str, int, str, str], ...] = (
    ("Por Uno", "ATA", "Por", 18, "0.90", "0.90"),
    ("Por Due", "BOL", "Por", 10, "0.90", "0.40"),
    ("Dif Uno", "ATA", "Dc", 22, "0.90", "0.85"),
    ("Dif Due", "BOL", "Dc", 16, "0.90", "0.70"),
    # Three interchangeable centre-backs, deliberately identical in every input the value
    # model reads. At most two of the three fit the twelve, so whichever makes it has a
    # perfect substitute on the bench — and `lot_ceiling` answers 0 for him, meaning "the
    # rosa does not improve by buying him". That is the `WALK_AWAY_HOLD` case, and without
    # a pair like this the seed cannot express it: `test_a_walk_away_of_zero_survives_
    # serialisation_as_zero` fails loudly rather than passing on an absent case.
    ("Dif Tre", "CAG", "Dc", 12, "0.85", "0.60"),
    ("Dif Qua", "ATA", "Dc", 12, "0.85", "0.60"),
    ("Dif Cin", "BOL", "Dc", 12, "0.85", "0.60"),
    ("Ter Uno", "BOL", "Dd", 8, "0.90", "0.50"),
    ("Est Uno", "ATA", "E", 20, "0.90", "0.80"),
    ("Est Due", "CAG", "E", 14, "0.90", "0.65"),
    ("Est Tre", "BOL", "E", 11, "0.85", "0.45"),
    ("Med Uno", "BOL", "M", 26, "0.90", "0.85"),
    ("Med Due", "CAG", "M", 13, "0.85", "0.55"),
    ("Cen Uno", "CAG", "C", 31, "0.90", "0.88"),
    ("Cen Due", "ATA", "C", 15, "0.85", "0.60"),
    # The two injured stars. Top of the pool on `fvm`, out for the season on availability.
    ("Ala Rotta", "ATA", "W", 44, "0.05", "0.10"),
    ("Ala Uno", "BOL", "W", 21, "0.90", "0.75"),
    ("Att Uno", "BOL", "A", 57, "0.88", "0.86"),
    ("Pun Rotta", "CAG", "Pc", 88, "0.05", "0.10"),
)

#: What the seeded lega declares. Twelve of eighteen, so the plan is a choice.
_ROSTER_SIZE = 12
_MIN_ROLES = (1, 11)
_MAX_ROLES = (2, 12)


#: The pricing model's corpus — a second world beside the asta pool above, and separate
#: from it on purpose.
#:
#: `application/pricing.py` pins **real** seasons: it trains on `TRAIN_SEASONS`
#: (2023/24-2025/26, read through the `qi_bias` *view* over `quotazioni` rather than a
#: table of its own) against `statistiche` for each of their prior seasons, and prices the
#: `TARGET_SEASON` universe. None of that can be written under `PARITY_SEASON`, so this
#: half of the seed cannot be swept by season the way the other half is.
#:
#: It is swept by **synthetic player id and synthetic club code** instead, neither of
#: which a scrape can produce. The club codes are load-bearing, not decoration:
#: `quotazioni` has a composite foreign key onto `teams (stagione, squadra)`, so the
#: corpus needs `teams` rows under 2022/23-2026/27 — and deleting *those* by season would
#: delete a real corpus's clubs on a machine that has one.
#:
#: **Without this corpus the property 1.16 names cannot be tested at all.**
#: `upsert_target_price` returns at `scraping.py:193` before issuing any SQL when handed
#: no rows, so on a corpus-less database the read-only transaction in
#: `test_the_get_writes_nothing` has nothing to refuse and the test passes whatever the
#: GET does. Four tests in `test_parity_pricing.py` opened with a skip for that reason;
#: the skips are gone and this is what replaces them.

#: Above `_POOL`'s block, so one sweep by id range covers both and the two cannot collide.
PRICING_BASE = SYNTHETIC_BASE + 1_000

#: Three characters, and deliberately not Serie A codes — see the note above. Also
#: deliberately not `NAP` or `MIL`: those are `pricing.TEAM_DISCOUNT_ALLOWLIST`, and
#: seeding them would mean a sweep that deletes real club rows under real season keys.
#: The team discount is therefore *not* exercised here, and `team_factors` is empty.
PRICING_CLUBS = ("ZZA", "ZZB", "ZZC")

#: One of the three `statistiche.fonte` the check constraint allows. One rather than
#: three: `load_prior_stats` averages across them, and a mean of one is the same number.
PRICING_FONTE = "fantacalcio"

#: Macro role -> the code each listone spells it with. `pricing.macro_role` lower-cases a
#: Classic code and takes the first `;` component of a Mantra one, so these are two
#: spellings of the same buckets. Stored upper-case, as the scrapers normalise them.
_MACRO_CODES = {
    "classic": {"DEF": "D", "MID": "C", "ATT": "A", "GK": "P"},
    "mantra": {"DEF": "DC", "MID": "M", "ATT": "A", "GK": "POR"},
}

#: Seven players per outfield macro role x three training seasons = 21 observations,
#: against `pricing.MIN_OBSERVATIONS = 20`. A role one observation short is dropped by
#: `fit_fades` in silence, which is why the count is asserted and not just the roles.
_PER_ROLE = 7

#: `training_pairs` drops keepers — goalkeepers showed ~0 correlation — so a `GK` fade is
#: not a thing the seed can produce, and a test expecting one would be wrong about the model.
_TRAINED_ROLES = ("DEF", "MID", "ATT")


def _prior_fantamedia(index: int, season_index: int) -> float:
    """The x of the fade's regression. **Non-constant on purpose.**

    `statistics.linear_regression` raises `StatisticsError: x is constant` on a cohort
    that all scored the same, so a flat fixture would not fit a fade — it would fail
    inside `fit_fades` with an error that reads like a model defect.
    """
    return round(5.0 + 0.25 * index + 0.1 * season_index, 2)


def _appearances(index: int) -> int:
    """Inside `pricing`'s validated 25-38 band.

    Outside it an observation is refused by `training_pairs` and a target player is
    flagged `thin_prior_sample_no_fade` instead of being faded — which is a real branch,
    exercised by `_FLAG_PLAYERS` below rather than by accident here.
    """
    return 26 + index


def _quote_pair(index: int, fantamedia: float) -> tuple[int, int]:
    """`(qi, qa)` for one training row, shaped like the effect the model fits.

    `qi` clears `MIN_QI` (strictly greater than 2 — below it the percentage drift is
    dominated by the divisor rather than by the market), and `qa` falls as the prior
    fantamedia rises, which is the regression-to-mean the fade exists to measure. `qa` is
    never 0: `training_pairs` drops those, since `log(0)` is undefined and a player written
    down to worthless is a data artefact rather than evidence about how quotazioni fade.
    """
    qi = 20 + 2 * index
    return qi, max(1, round(qi * math.exp(0.90 - 0.15 * fantamedia)))


#: The four `price_universe` branches that are not the fade, one player each, present in
#: the target season only. They are here so the report carries every flag an operator can
#: be shown, not to test the model: one player reaches exactly one branch, so this fixture
#: says nothing about the chain's **precedence** — `tests/application/test_pricing.py:243`
#: is what pins that ("a cheap keeper reads `floor_qi`, not `goalkeeper`"), and reordering
#: the `elif`s leaves this tier green. What the tier does pin is that the four branches the
#: seed declares are the four the model actually produces from it.
#:
#: `(suffix, macro role, qi, prior appearances or None for no prior at all, flag)`
_FLAG_PLAYERS: tuple[tuple[int, str, int, int | None, str], ...] = (
    (0, "DEF", 2, 30, "floor_qi"),
    (1, "GK", 15, 30, "goalkeeper_no_fade"),
    (2, "MID", 18, None, "no_prior_data"),
    (3, "ATT", 25, 10, "thin_prior_sample_no_fade"),
)


def _seed_pricing_corpus(
    session: Session,
) -> tuple[tuple[str, ...], int, int, tuple[str, ...]]:
    """Write the training corpus and the target universe, for both listoni.

    Returns `(trained macro roles, observations per role, universe size, flags)` so
    `SeededWorld` can carry them and a test can assert against the seed rather than
    against a literal that drifts away from it.

    The season names are read from `pricing` rather than restated: the model pins
    2026/27 today and will pin 2027/28 one August, and a fixture holding its own copy
    would go quietly empty on the day it moved — which is the corpus-less state this
    whole exercise exists to make impossible.
    """
    from fantabot.application import pricing

    seasons = {*pricing.TRAIN_SEASONS, *pricing.PREV_OF_TRAIN.values(),
               pricing.TARGET_SEASON, pricing.PRIOR_SEASON_FOR_TARGET}
    session.execute(
        text(
            "INSERT INTO teams (stagione, codice, nome_completo) VALUES (:s, :c, :n) "
            "ON CONFLICT DO NOTHING"
        ),
        [{"s": s, "c": c, "n": f"Pricing {c}"}
         for s in sorted(seasons) for c in PRICING_CLUBS],
    )

    cohort = [
        (PRICING_BASE + role_index * _PER_ROLE + i, macro, i)
        for role_index, macro in enumerate(_TRAINED_ROLES)
        for i in range(_PER_ROLE)
    ]
    flagged = [
        (PRICING_BASE + 100 + suffix, macro, qi, partite, flag)
        for suffix, macro, qi, partite, flag in _FLAG_PLAYERS
    ]

    session.execute(
        text("INSERT INTO players (id, nome) VALUES (:i, :n) ON CONFLICT (id) DO NOTHING"),
        [{"i": pid, "n": f"Fit {macro} {i}"} for pid, macro, i in cohort]
        + [{"i": pid, "n": f"Flag {flag}"} for pid, _m, _q, _p, flag in flagged],
    )

    quotes: list[dict[str, object]] = []
    stats: list[dict[str, object]] = []
    for listone, codes in _MACRO_CODES.items():
        for pid, macro, i in cohort:
            club = PRICING_CLUBS[i % len(PRICING_CLUBS)]
            code = codes[macro]
            # One training observation per season: a `quotazioni` row the `qi_bias` view
            # turns into drift, and a `statistiche` row for that season's *prior*, which
            # is the x the fade is fitted on.
            for k, train_season in enumerate(pricing.TRAIN_SEASONS):
                fantamedia = _prior_fantamedia(i, k)
                qi, qa = _quote_pair(i, fantamedia)
                quotes.append({"s": train_season, "i": pid, "l": listone, "sq": club,
                               "rc": [code], "qi": qi, "qa": qa, "f": qa})
                stats.append({"s": pricing.PREV_OF_TRAIN[train_season], "i": pid,
                              "l": listone, "sq": club, "rc": [code],
                              "pg": _appearances(i), "mf": fantamedia})
            # The same players are the target universe, so every one of them reaches the
            # fade branch rather than a flag — `PRIOR_SEASON_FOR_TARGET` is the prior the
            # pricing half reads, and it is not any training season's prior.
            target_fantamedia = _prior_fantamedia(i, len(pricing.TRAIN_SEASONS))
            qi, _qa = _quote_pair(i, target_fantamedia)
            quotes.append({"s": pricing.TARGET_SEASON, "i": pid, "l": listone, "sq": club,
                           "rc": [code], "qi": qi, "qa": qi, "f": qi})
            stats.append({"s": pricing.PRIOR_SEASON_FOR_TARGET, "i": pid, "l": listone,
                          "sq": club, "rc": [code], "pg": _appearances(i),
                          "mf": target_fantamedia})

        for pid, macro, qi, partite, _flag in flagged:
            club = PRICING_CLUBS[0]
            code = codes[macro]
            quotes.append({"s": pricing.TARGET_SEASON, "i": pid, "l": listone, "sq": club,
                           "rc": [code], "qi": qi, "qa": qi, "f": qi})
            if partite is not None:
                stats.append({"s": pricing.PRIOR_SEASON_FOR_TARGET, "i": pid, "l": listone,
                              "sq": club, "rc": [code], "pg": partite, "mf": 6.0})

    session.execute(
        text(
            "INSERT INTO quotazioni "
            "(stagione, player_id, listone, squadra, ruoli_codice, ruoli, qi, qa, fvm) "
            "VALUES (:s, :i, :l, :sq, :rc, :rc, :qi, :qa, :f) ON CONFLICT DO NOTHING"
        ),
        quotes,
    )
    session.execute(
        text(
            "INSERT INTO statistiche (stagione, fonte, player_id, listone, squadra, "
            "ruoli_codice, ruoli, partite_giocate, media_voto, media_fantavoto, gol, "
            "gol_subiti, rigori_segnati, rigori_tirati, rigori_parati, assist, "
            "ammonizioni, espulsioni) VALUES (:s, :fonte, :i, :l, :sq, :rc, :rc, :pg, "
            ":mf, :mf, 0, 0, 0, 0, 0, 0, 0, 0) ON CONFLICT DO NOTHING"
        ),
        [{**row, "fonte": PRICING_FONTE} for row in stats],
    )
    return (
        _TRAINED_ROLES,
        _PER_ROLE * len(pricing.TRAIN_SEASONS),
        len(cohort) + len(flagged),
        tuple(flag for _s, _m, _q, _p, flag in _FLAG_PLAYERS),
    )


def _seed(session: Session) -> SeededWorld:
    """Write one small, complete world: a pool, a priced corpus, and a lega.

    Committed, not rolled back — see the module docstring. Every row is keyed into a
    season, a league id and an auction id that exist nowhere else, so the sweep in
    `seeded_db` can be exact rather than a `TRUNCATE`.
    """
    ids = tuple(str(SYNTHETIC_BASE + n) for n in range(len(_POOL)))
    # `quotazioni` has a composite foreign key onto `teams (stagione, squadra)`, so the
    # three clubs the pool spreads over have to exist first. The spread is not decoration:
    # `lam` diversifies across clubs, and a single-club pool would make that term inert
    # and hide a divergence in it.
    for codice in sorted({row[1] for row in _POOL}):
        session.execute(
            text(
                "INSERT INTO teams (stagione, codice, nome_completo) VALUES (:s, :c, :n) "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": PARITY_SEASON, "c": codice, "n": f"Parity {codice}"},
        )
    for pid, (nome, squadra, ruolo, fvm, _disp, _tit) in zip(ids, _POOL, strict=True):
        session.execute(
            text("INSERT INTO players (id, nome) VALUES (:i, :n) ON CONFLICT (id) DO NOTHING"),
            {"i": int(pid), "n": nome},
        )
        session.execute(
            text(
                "INSERT INTO quotazioni "
                "(stagione, player_id, listone, squadra, ruoli_codice, ruoli, qi, qa, fvm) "
                "VALUES (:s, :i, 'mantra', :sq, :rc, :r, :q, :q, :f) "
                "ON CONFLICT DO NOTHING"
            ),
            {"s": PARITY_SEASON, "i": int(pid), "sq": squadra,
             "rc": [ruolo], "r": [ruolo], "q": max(1, fvm // 4), "f": fvm},
        )

    # A sentiment run for the frozen date, one row per player. Not optional decoration:
    # `sentiment_rows` **refuses** an empty result — "valuing on no rows is numerically
    # identical to --no-sentiment but means something entirely different" — so without
    # this the CLI raises `BadParameter` and the parity comparison never happens. Which is
    # the whole divergence in miniature: the endpoint passes `sentiment=None` and is
    # perfectly happy planning on the ablation control.
    #
    # `confidenza` is deliberately non-zero: a 0 means "no coverage was found", and
    # `NewsSentimentSource` excludes those rows from every average by design.
    for pid, (nome, squadra, ruolo, _fvm, disp, tit) in zip(ids, _POOL, strict=True):
        session.execute(
            text(
                "INSERT INTO player_sentiment (data_run, player_id, giorni_lookback, "
                "stagione, nome, squadra, ruolo, ruoli_mantra, ruolo_campo, deriva_ruolo, "
                "sentiment, disponibilita, titolarita, mercato, forma, rigorista, piazzati, "
                "confidenza, riassunto, n_fonti, fonti, modello) "
                "VALUES (:run, :i, 7, :s, :n, :sq, :ru, :ru, :ru, 0, "
                "0.60, :disp, :tit, 0.50, 0.60, 0.10, 0.10, 0.70, 'parity seed', 1, "
                "ARRAY['parity'], 'parity') ON CONFLICT DO NOTHING"
            ),
            {"run": FROZEN_TODAY, "i": int(pid), "s": PARITY_SEASON,
             "n": nome, "sq": squadra, "ru": ruolo, "disp": disp, "tit": tit},
        )

    # One recorded 8x500 Mantra room, so `clearing_sales` has a corpus to average. Without
    # it `prices` is empty, `DEFAULT_PRICE = 1` applies to everyone, and the budget
    # constraint is vacuous — the 2026-09-05 incident, reproduced in a fixture.
    session.execute(
        text(
            "INSERT INTO asta (id, fantaleague_id, db_shard, asta_type, name, "
            "num_teams, num_credits) VALUES (:a, :fl, 'parity', 'mantra', 'parity room', 8, 500) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"a": _AUCTION_ID, "fl": str(PARITY_LEAGUE)},
    )
    for index, (pid, row) in enumerate(zip(ids, _POOL, strict=True)):
        fvm = row[3]
        session.execute(
            text(
                "INSERT INTO asta_assignment "
                "(asta_id, player_uuid, fantacalcio_id, price, buyer_team_id, ladder) "
                "VALUES (:a, :u, :i, :p, 'parity-buyer', '[]'::jsonb) ON CONFLICT DO NOTHING"
            ),
            {"a": _AUCTION_ID, "u": f"parity-{index}", "i": int(pid), "p": max(1, fvm // 2)},
        )

    session.execute(
        text(
            "INSERT INTO league_snapshot (captured_at, league_id, budget, roster_size, "
            "role_groups, min_roles, max_roles, modules, bench_size, matchday) "
            "VALUES (:t, :l, 500, :size, 2, :mn, :mx, :mods, 4, 1) ON CONFLICT DO NOTHING"
        ),
        {"t": _CAPTURED_AT, "l": PARITY_LEAGUE, "size": _ROSTER_SIZE, "mn": list(_MIN_ROLES),
         "mx": list(_MAX_ROLES), "mods": ["343", "352"]},
    )
    session.execute(
        text(
            "INSERT INTO league_team_snapshot (captured_at, league_id, team_id, nome, owner, "
            "credits_initial, credits_spent, credits_remaining, roster_ids, roster_costs) "
            "VALUES (:t, :l, 1, 'Parity FC', 'parity', 500, 100, 400, :ids, :costs) "
            "ON CONFLICT DO NOTHING"
        ),
        {"t": _CAPTURED_AT, "l": PARITY_LEAGUE,
         "ids": [int(p) for p in ids[:3]], "costs": [10, 20, 70]},
    )
    # The earlier capture: the same lega, a different day, a different team count. Written
    # after the current one so a reader that sorts wrongly still gets a definite answer.
    session.execute(
        text(
            "INSERT INTO league_snapshot (captured_at, league_id, budget, roster_size, "
            "role_groups, min_roles, max_roles, modules, bench_size, matchday) "
            "VALUES (:t, :l, 400, 30, 2, :mn, :mx, :mods, 4, 1) ON CONFLICT DO NOTHING"
        ),
        {"t": _EARLIER_AT, "l": PARITY_LEAGUE, "mn": [2, 28], "mx": [2, 28],
         "mods": ["343"]},
    )
    for team_id, nome in ((7, "Stale FC"), (8, "Stale United")):
        session.execute(
            text(
                "INSERT INTO league_team_snapshot (captured_at, league_id, team_id, nome, "
                "owner, credits_initial, credits_spent, credits_remaining, roster_ids, "
                "roster_costs) VALUES (:t, :l, :tid, :n, 'stale', 400, 0, 400, :ids, :costs) "
                "ON CONFLICT DO NOTHING"
            ),
            {"t": _EARLIER_AT, "l": PARITY_LEAGUE, "tid": team_id, "n": nome,
             "ids": [], "costs": []},
        )

    # The pricing corpus, under the model's own real seasons. Separate from everything
    # above and swept by id and club code rather than by season — see its own note.
    pricing_roles, pricing_observations, pricing_universe, pricing_flags = (
        _seed_pricing_corpus(session)
    )

    session.commit()
    return SeededWorld(
        season=PARITY_SEASON,
        league_id=PARITY_LEAGUE,
        listone="mantra",
        player_ids=ids,
        budget=500,
        num_teams=8,
        roster_size=_ROSTER_SIZE,
        min_roles=_MIN_ROLES,
        pricing_roles=pricing_roles,
        pricing_observations=pricing_observations,
        pricing_universe=pricing_universe,
        pricing_flags=pricing_flags,
    )



def _sweep(session: Session) -> None:
    """Remove exactly what `_seed` wrote. Not a `TRUNCATE`: this database is shared with
    the `db` tier, which has its own synthetic rows and its own reasons to keep them."""
    for statement, params in (
        ("DELETE FROM asta_assignment WHERE asta_id = :a", {"a": _AUCTION_ID}),
        ("DELETE FROM asta WHERE id = :a", {"a": _AUCTION_ID}),
        # By player id, not by season. The pricing corpus is written under the model's
        # own real seasons, and `run()` upserts `target_price` under `TARGET_SEASON` —
        # neither of which a `stagione = PARITY_SEASON` predicate reaches. The id range
        # is exact in a way a season predicate cannot be here, and it leaves a real
        # corpus's rows alone on a machine that has one.
        #
        # It also cannot reach the `db` tier's rows, which share this database: that
        # tier's `SYNTHETIC_PLAYER_BASE` is 9_100_000_000 and this one's `SYNTHETIC_BASE`
        # is 9_200_000_000, so the two blocks are disjoint and `>=` here stops above
        # theirs. Widening this predicate to `>= 9_100_000_000` would delete them.
        ("DELETE FROM target_price WHERE player_id >= :b", {"b": SYNTHETIC_BASE}),
        ("DELETE FROM player_sentiment WHERE player_id >= :b", {"b": SYNTHETIC_BASE}),
        ("DELETE FROM statistiche WHERE player_id >= :b", {"b": SYNTHETIC_BASE}),
        ("DELETE FROM quotazioni WHERE player_id >= :b", {"b": SYNTHETIC_BASE}),
        ("DELETE FROM teams WHERE stagione = :s", {"s": PARITY_SEASON}),
        # The pricing corpus's clubs, under five real seasons. Deleted by code rather
        # than by season for exactly that reason — see `PRICING_CLUBS`.
        ("DELETE FROM teams WHERE codice = ANY(:c)", {"c": list(PRICING_CLUBS)}),
        ("DELETE FROM league_team_snapshot WHERE league_id = :l", {"l": PARITY_LEAGUE}),
        ("DELETE FROM league_snapshot WHERE league_id = :l", {"l": PARITY_LEAGUE}),
        ("DELETE FROM players WHERE id >= :b", {"b": SYNTHETIC_BASE}),
    ):
        session.execute(text(statement), params)
    session.commit()


@pytest.fixture
def seeded_db(parity_db: None) -> Generator[SeededWorld, None, None]:
    """One committed world, swept afterwards whatever the test did."""
    from fantabot.adapters.persistence import database_manager

    with database_manager.get_session() as session:
        _sweep(session)  # a previous run that died before its teardown
        world = _seed(session)
    try:
        yield world
    finally:
        with database_manager.get_session() as session:
            _sweep(session)


@pytest.fixture(autouse=True)
def listone_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """FantaLab's `uuid -> fantacalcio_id` bridge, covering exactly the seeded pool.

    Autouse, and load-bearing rather than tidy. Both sides narrow the pool to the players
    the listone can call, so an unpatched `fetch` reaches a CDN — or, worse, a *gitignored
    local cache* — and returns 530 real ids, none of which is a synthetic one. The plan
    then has no pool at all and both sides agree that there is nothing to buy, which is the
    "two empty results agree perfectly" failure in its most convincing form: `found=false`
    with a plausible reason.

    Patched here for the same reason `tests/_golden.py` patches it as its sixth point: a
    gate whose answer depends on a machine's cache is a gate that passes for the wrong
    reason on the machine that wrote it.
    """
    from fantabot.adapters.http.fantalab import listone

    bridge = {f"parity-uuid-{n}": SYNTHETIC_BASE + n for n in range(len(_POOL))}
    monkeypatch.setattr(listone, "fetch", lambda *args, **kwargs: dict(bridge))


@pytest.fixture
def seeded_callable_ids() -> frozenset[str]:
    """What that bridge narrows to — the CLI side of the comparison uses it too."""
    return frozenset(str(SYNTHETIC_BASE + n) for n in range(len(_POOL)))


@pytest.fixture
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Both calendar seams, pinned to one date.

    One per surface, which `tests/domain/asta/test_asta_clock.py` enforces — the reason
    that test had to be extended before this tier could exist. A surface with two seams is
    one this fixture silently half-freezes, and a surface whose seam this fixture does not
    know about is one it does not freeze at all.
    """
    from fantabot.interface import asta as asta_cli
    from fantabot.interface import lineup as lineup_cli

    from fantabot_app.api.v1.endpoints import asta as asta_endpoint
    from fantabot_app.api.v1.endpoints import lineup as lineup_endpoint

    monkeypatch.setattr(asta_cli, "_today", lambda: FROZEN_TODAY)
    # The app's own seam, created by 1.5. Leaving it out is not a small omission: the two
    # sides then read the calendar six days apart, the 7-day confidence decay rescales
    # every reading, and the plans differ in `objective` while agreeing on membership —
    # a divergence that looks exactly like the one this tier is meant to catch.
    monkeypatch.setattr(asta_endpoint, "_today", lambda: FROZEN_TODAY)
    # Naive, mirroring what the seam actually returns: `_now` is `datetime.now()` and it
    # is compared against the platform's own naive matchday strings. A tz-aware stand-in
    # would freeze the clock and change the comparison in the same breath.
    frozen_now = lambda: datetime(  # noqa: DTZ001 — naive, as the seam returns
        FROZEN_TODAY.year, FROZEN_TODAY.month, FROZEN_TODAY.day
    )
    monkeypatch.setattr(lineup_cli, "_now", frozen_now)
    # The app's lineup seam, added by 3.3 with `POST /lineup/submit`'s kickoff warning. A
    # surface whose seam this fixture does not know about is one it does not freeze.
    monkeypatch.setattr(lineup_endpoint, "_now", frozen_now)
    return FROZEN_TODAY


@pytest.fixture
def cli() -> Callable[..., Result]:
    """Run a `fantabot` command in *this* process, and fail loudly if it did not run.

    `CliRunner` swallows a non-zero exit into `result.exit_code`, so a command that died
    on a missing table would otherwise be compared against the endpoint as if it had
    produced an answer — two empty results agreeing about nothing.
    """
    from fantabot.interface.app import app as fantabot_cli

    runner = CliRunner()

    def run(*args: str, expect_exit: int | None = 0) -> Result:
        result = runner.invoke(fantabot_cli, list(args))
        if expect_exit is None:  # the caller reads the outcome another way
            return result
        assert result.exit_code == expect_exit, (
            f"`fantabot {' '.join(args)}` exited {result.exit_code}, expected "
            f"{expect_exit}\n{result.output}\n{result.exception!r}"
        )
        return result

    return run


@pytest.fixture
def api() -> Generator[TestClient, None, None]:
    """The app, in the same process as the CLI and over the same `database_manager`."""
    with TestClient(app) as client:
        yield client


@contextmanager
def cli_session() -> Iterator[Session]:
    """A session the way a command gets one — through the shared manager, not a new engine."""
    from fantabot.adapters.persistence import database_manager

    with database_manager.get_session() as session:
        yield session


def env_says_where() -> str:
    """For a skip message: which database the tier was pointed at, without a password."""
    return os.environ.get("FANTABOT_TEST_DATABASE_URL", "<unset — the bundled default>")
