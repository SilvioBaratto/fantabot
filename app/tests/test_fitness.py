"""Architecture fitness tests — the load-bearing invariants (SPEC A6, A7).

Static source scans (no imports of heavy deps, no sockets): they read the tracked source
of the fantabot_app package and assert the two properties the SPEC promises.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import fantabot_app


def _package_root() -> Path:
    return Path(fantabot_app.__file__).parent


def _source_files(*, under: Path, include_tests: bool = False) -> list[Path]:
    """Every tracked module under *under*, tests excluded by default.

    ``include_tests`` is a parameter and not the new default, and that distinction is
    load-bearing. This helper is shared by every fitness test in the file, the acting ban
    among them — and the acting ban is a substring scan for names like ``place_raise(``,
    which the tests for an acting path will legitimately contain. Widening the scan here
    would silently point that ban at its own test suite the day the first acting endpoint
    is written.

    Exactly one caller opts in: the second-engine test, whose subject *is* the test
    conftest.
    """
    return [
        py
        for py in under.rglob("*.py")
        if "__pycache__" not in py.parts and (include_tests or "tests" not in py.parts)
    ]


def test_api_holds_no_second_sqlalchemy_engine() -> None:
    """A6 — every DB session comes from fantabot's single ``database_manager``.

    The HTTP adapter (``fantabot_app.api``) must not build its own engine or sessionmaker.
    (The provisioner is deliberately excluded: it uses a *transient* admin engine only to
    CREATE the database before fantabot connects, and disposes it immediately.)

    **``api/tests/`` is scanned too, and it used to be exempt.** The exemption was
    covering the only ``create_engine``/``sessionmaker`` pair anywhere under ``api/``:
    the test conftest built a SQLite engine to ``create_all`` an empty ``DeclarativeBase``
    and override a ``get_db`` no route depends on — zero tables, an override that
    overrode nothing, and precisely what this test forbids. The scaffold is gone and the
    exemption with it.
    """
    api_root = _package_root() / "api"
    offenders: list[str] = []
    for py in _source_files(under=api_root, include_tests=True):
        text = py.read_text(encoding="utf-8")
        if "create_engine(" in text or re.search(r"\bsessionmaker\(", text):
            offenders.append(str(py.relative_to(_package_root())))
    assert offenders == [], f"second engine in the API adapter: {offenders}"


def test_app_never_handles_a_plaintext_token() -> None:
    """A7 — the app hands sessions/stores to fantabot; fantabot decrypts internally.

    No app module reads a plaintext bearer token or calls decrypt itself, so no token can
    be logged, returned, or repr'd from app code.
    """
    root = _package_root()
    forbidden = ("load_plaintext", ".decrypt(")
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=root):
        text = py.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                offenders.append((str(py.relative_to(root)), term))
    assert offenders == [], f"app code handling plaintext tokens: {offenders}"


ACTING_NAMES = (
    "decide_bid(",  # chooses a raise
    "place_raise(",  # PATCHes it to the RTDB — the one that spends credits
    "run_bid_loop(",  # the loop that calls both, forever
    "RoomTracker(",  # owns the loop and the per-cycle decision
    "teamLineup_submit(",  # POSTs the weekly lineup
)
"""The names no app module may contain. A module-level tuple, not a local, so the guard
below can read the **object** instead of grepping the file that defines it.

`teamLineup_submit(` was taken off this list in `fbf39f1`, the commit that built
`POST /lineup/submit`, on the reasoning that "never" had stopped being true. It had not:
the route calls `application.lineup_submit.submit_lineup`, and never names the adapter
write. Nothing in the app package matches this string outside its own tests, which
`_source_files` excludes by default for exactly this reason.

Restoring it is what makes the ban say the useful thing. A substring list cannot express
"only behind two locks" — but it can express "only through `application/`", which is the
property that matters: `submit_lineup` is where the arming contract lives, and an endpoint
that reached `apileague.teamLineup_submit` directly would submit a real lineup with no
locks at all. Between `fbf39f1` and now, nothing said so.
"""


def test_no_bid_or_lineup_submit_wiring_exists() -> None:
    """The app never names a function that acts. It reaches one only through `application/`.

    **Re-cut to ban the write path rather than a module name.** The list used to hold
    ``"application.asta_room"``, which is the module that also defines ``resolve_room``,
    ``RoomRefused`` and ``RoomFrame`` — none of which can bid. So a read-only room view
    failed this test, while ``rtdb.place_raise`` and ``room.run_bid_loop``, the two
    functions that actually spend credits, were absent from the list and would have
    passed. The guard banned the viewer and permitted the bidder.

    ``RoomTracker`` is on the list because its ``cycle`` is what decides and places a
    raise; reading a room's configuration is not the same act and is deliberately allowed.
    """
    root = _package_root()
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=root):
        text = py.read_text(encoding="utf-8")
        for term in ACTING_NAMES:
            if term in text:
                offenders.append((str(py.relative_to(root)), term))
    assert offenders == [], f"the app wires an action it must not take: {offenders}"


def test_the_boundary_names_the_functions_that_actually_act() -> None:
    """A guard that misses the write path is worse than none — it reassures.

    This read the file's own *text* and so could not fail. ``for acting in ("place_raise(",
    ...)`` puts those literals in the source it then searched, so the assertion found its
    own loop header whatever the ban contained; deleting ``place_raise(`` and
    ``run_bid_loop(`` from the list left every assertion green. The second assertion was
    worse: ``source.split("forbidden = (")[1]`` split on **three** occurrences of that
    string and landed on the plaintext-token ban, so the acting list was never inspected at
    all.

    It now reads :data:`ACTING_NAMES` — the object the scan actually uses. The literals here
    are the expectation, which is the point of a meta-guard; what changed is that they are
    checked against the list rather than against the file that contains them both.
    """
    must_be_banned = (
        "place_raise(",  # PATCHes a raise to the RTDB — the one that spends credits
        "run_bid_loop(",  # the loop that calls both, forever
        "decide_bid(",  # chooses a raise
        "RoomTracker(",  # owns the loop and the per-cycle decision
        "teamLineup_submit(",  # POSTs the weekly lineup
    )
    missing = [name for name in must_be_banned if name not in ACTING_NAMES]

    assert missing == [], f"dropped from the acting boundary: {missing}"
    # Every banned name pinned, not a chosen few. `RoomTracker(` was on the ban and off
    # this list, so it could be dropped silently — recorded under 3.11 and then carried
    # past by the commit that rewrote this guard.
    assert set(must_be_banned) == set(ACTING_NAMES), (
        f"the ban and its pin disagree: {set(ACTING_NAMES) ^ set(must_be_banned)}"
    )


def test_no_app_module_reaches_the_cap() -> None:
    """**The MAX cap stays the last line of defence, unweakened and unwrapped.**

    Nothing on the platform enforces it: `docs/fantalab/01:142` calls it client-enforced and
    `06:389-412` shows the RTDB rules validating only that a raise exceeds the current price
    and names the right lot. It is the only thing between a "pay anything" walk-away and a
    rosa that cannot be fielded, and `reservations` really does return the whole remaining
    budget for a target whose removal makes the roster infeasible.

    So the app may not compute one — not to raise it, not to default it, and not to wrap it
    in a route's own ceiling. The child computes it from the room's band and applies it
    inside the loop, where this package has no seam to reach it.

    **Read as syntax, not as text, and it earned that on its first run.** A substring scan
    for `max_cap` hit two innocents immediately: this rule written down in
    `room_bid.py`'s own docstring, and `asta.py`'s `JournalRow.max_cap` — the *recorded*
    number a viewer renders, which is the cap being reported, not applied. That is the exact
    failure 3.11 is about: a scan that cannot tell a call from a sentence about a call, or
    from a field that happens to share its name. An import is banned alongside the call
    because a name bound but not yet used is the commit before the one that uses it.
    """
    acting = {"max_cap", "max_bid"}
    offenders: list[tuple[str, str]] = []
    for py in _source_files(under=_package_root()):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        where = str(py.relative_to(_package_root()))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name in acting:
                    offenders.append((where, f"calls {name}()"))
            elif isinstance(node, ast.ImportFrom):
                offenders += [
                    (where, f"imports {alias.name}")
                    for alias in node.names
                    if alias.name in acting
                ]
    assert offenders == [], f"the app computes a cap of its own: {offenders}"


def test_every_get_server_call_states_the_cleanup_mode() -> None:
    """The bundled server's lifetime is explicit, and pgserver's default is not.

    ``get_server(pgdata)`` defaults to ``cleanup_mode='stop'``: an ``atexit`` hook stops
    the server when the last handle-holding process exits, so a ``db start`` that omitted
    it would print a DSN to a server that died with the command. Worse, ``get_server``
    caches per pgdata and *ignores* the argument on a cache hit, so one bare call anywhere
    in the package decides the mode for the whole process.

    Read as syntax, not as text: ``_default_postmaster``'s docstring explains why
    ``get_server(..., start=False)`` is the wrong tool, and a grep would fail on the
    explanation.
    """
    offenders: list[str] = []
    for py in _source_files(under=_package_root()):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "get_server":
                continue
            if not any(kw.arg == "cleanup_mode" for kw in node.keywords):
                offenders.append(f"{py.relative_to(_package_root())}:{node.lineno}")
    assert offenders == [], f"get_server without an explicit cleanup_mode: {offenders}"


def _frontend_source() -> Path | None:
    """`app/frontend/src`, or None when only the compiled bundle is installed.

    The wheel ships `fantabot_app/web/` and not the Angular sources, so a test that
    asserted the directory exists would fail for an end user running the suite from an
    installed copy. Skipping there is right; skipping in the repository is not, which is
    why this returns a path rather than a boolean.
    """
    source = Path(fantabot_app.__file__).parent.parent / "frontend" / "src"
    return source if source.is_dir() else None


def test_the_news_fetch_trigger_has_exactly_one_home() -> None:
    """One button, on the News page. Not two, and not zero.

    It was removed from Synchronize at the operator's request while
    `ActionsService.runNewsFetch` and `POST /actions/news-fetch` were deliberately kept —
    which left the app with an endpoint nothing could reach. Rehoused on News (the
    archived phase spec §7: the topic's own page, so nine nav tabs became ten and not
    eleven — `tasks/archive/fantalab-in-the-app-spec.md`).

    A second copy is the failure this guards: two buttons for a 523-player run through the
    Agent SDK are two runs, and the second one is not free.
    """
    source = _frontend_source()
    if source is None:  # installed from the wheel — sources are not shipped
        return

    # ``as_posix()``, not ``str()``: the expected value below is a POSIX literal, and on
    # Windows ``str(Path)`` is backslash-separated -- so this assertion failed the whole
    # ``app-ci`` Windows job on nothing but the separator
    # (``['app\\pages\\news\\news.ts'] == ['app/pages/news/news.ts']``). It also
    # retires the ``.replace("\\", "/")`` that the ``/api/`` filter was carrying to work
    # around the same thing one line down.
    callers = sorted(
        ts.relative_to(source).as_posix()
        for ts in source.rglob("*.ts")
        if ".spec." not in ts.name
        and "/api/" not in ts.relative_to(source).as_posix()
        and "runNewsFetch(" in ts.read_text(encoding="utf-8")
    )

    assert callers == ["app/pages/news/news.ts"], f"news fetch is triggered from: {callers}"


def _league_read_surface() -> tuple[frozenset[str], frozenset[str]]:
    """The model classes and table names ``application/lega_reads.py`` owns.

    Derived from ``SNAPSHOT_TABLES`` — the tuple the reader itself iterates — and not
    re-listed here, on the same reasoning as :data:`ACTING_NAMES` being read as an object:
    a sixth snapshot table is covered by the guard on the day it is added to the reader,
    not on the day somebody remembers this file.
    """
    from fantabot.application.lega_reads import SNAPSHOT_TABLES

    models = {model.__name__ for _, model in SNAPSHOT_TABLES}
    tables = {name for name, _ in SNAPSHOT_TABLES}
    # `league_fixture` is deliberately not in that tuple — it is not a snapshot: it upserts
    # and has no `captured_at`, so `capture_inventory` counts it separately. It is still a
    # league read, and its join is the piece most worth having in one place: a fixture has
    # no `league_id` at all, and `_show` once counted it with no WHERE clause.
    return frozenset(models | {"LeagueFixture"}), frozenset(tables | {"league_fixture"})


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """The ``id()`` of every docstring constant in *tree*.

    The table-name scan below has to skip them, and skipping them is what makes this guard
    livable rather than a thing people delete. ``endpoints/lega.py`` names both
    ``LeagueSnapshot`` and ``LeagueTeamSnapshot`` in prose, to say which rows its response
    models map — that is the guard working (it reaches them through `lega_reads`), and a
    substring grep would call it the offence.
    """
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return docstrings


def test_the_app_holds_no_second_hand_written_league_read() -> None:
    """1.10's other half — the app reaches a lega's snapshots only through `lega_reads`.

    `api/reads/league.py` hand-wrote SQLAlchemy over the same snapshot models `lega show`
    hand-wrote it over, in two places, with no shared function, and the two could not be
    checked against each other. It is deleted and both surfaces now call
    `application/lega_reads.py` — but **`api/reads/` being gone was the only evidence**,
    and a directory being absent is not a guard. Nothing failed when a second copy came
    back.

    Two routes back in, so two things are banned:

    * **Naming a snapshot ORM model.** Read as syntax and not as text, because the only
      current mentions are docstrings in `endpoints/lega.py` and a grep would ban the
      compliant file. Importing one of these models *is* hand-writing the read: they carry
      no behaviour, so there is nothing else to import them for.
    * **Naming a snapshot table in a string.** `text("SELECT ... FROM league_snapshot")`
      evades every model check, and is the shorter path for somebody in a hurry.

    `LeagueRepository` is untouched and stays legal: it is write-only, which is the real
    reason a read helper exists at all. This bans a second *read*, not a write.

    **`tests/` is excluded** (`_source_files`' default) and that is load-bearing here: the
    parity seed writes its two captures with raw `INSERT INTO league_team_snapshot`, which
    is a fixture building the rows the guard exists to protect, not a surface reading them.
    """
    models, tables = _league_read_surface()
    # The surface itself, pinned. A `SNAPSHOT_TABLES` that shrank to nothing — or a helper
    # that quietly returned two empty sets — would leave every assertion below green while
    # checking nothing, which is the exact failure this phase produced six times.
    assert {"LeagueSnapshot", "LeagueTeamSnapshot", "LeagueFixture"} <= models, models
    assert {"league_snapshot", "league_team_snapshot", "league_fixture"} <= tables, tables

    root = _package_root()
    offenders: list[str] = []
    for py in _source_files(under=root):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        where = py.relative_to(root).as_posix()
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").endswith("models.league"):
                    offenders.append(f"{where}:{node.lineno}: imports from models.league")
                for alias in node.names:
                    if alias.name in models:
                        offenders.append(f"{where}:{node.lineno}: imports {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("models.league"):
                        offenders.append(f"{where}:{node.lineno}: imports {alias.name}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for table in sorted(tables):
                    if table in node.value:
                        offenders.append(f"{where}:{node.lineno}: SQL naming {table}")

    assert offenders == [], f"a second hand-written league read is back: {offenders}"
