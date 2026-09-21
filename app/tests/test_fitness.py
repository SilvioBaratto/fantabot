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


#: The names that **act**: they decide a raise, PATCH one to the RTDB, drive the loop that
#: does both, or POST a lineup. No app module may call one or import one.
#:
#: **Read as syntax, never as text** — that is what 3.11 replaced. The scan this succeeds was
#: a substring search over the app's source, so a docstring explaining the rule tripped it and
#: a Pydantic field that happened to share a name tripped it too. Both happened, within one
#: commit of each other, on `max_cap`.
#:
#: **Ban the write path, not a module name.** The first version banned
#: `application.asta_room`, which also holds the read-only `resolve_room` and `RoomFrame` —
#: so a room *viewer* failed while `rtdb.place_raise` and `room.run_bid_loop`, the two
#: functions that actually spend credits, were absent from the list and passed. It banned the
#: viewer and permitted the bidder.
ACTING_NAMES = (
    "decide_bid",  # chooses a raise
    "place_raise",  # PATCHes it to the RTDB — the one that spends credits
    "run_bid_loop",  # the loop that calls both, forever
    "RoomTracker",  # owns the loop and the per-cycle decision
    "teamLineup_submit",  # POSTs the weekly lineup
)

#: The arming decision, by name. `application/arming.decide_arming` is the only thing in this
#: repository allowed to answer "may this act", and `submit_lineup` is the one use case that
#: calls it for the caller.
ARMING_NAMES = ("decide_arming", "submit_lineup")

#: The argv token that arms a supervised child. A literal, because it is one: `--arm` is
#: present or absent and never a value — `--arm=false` reads as armed at a glance and is one
#: edit from open.
ARM_FLAG = "--arm"


def _app_trees() -> list[tuple[str, ast.Module]]:
    root = _package_root()
    return [
        # `as_posix`, never `str`: the two consumers compare this against `REQUEST_SURFACE`,
        # and on Windows a backslash path matches no prefix at all. See
        # `_on_request_surface` for what each of them did about that.
        (py.relative_to(root).as_posix(), ast.parse(py.read_text(encoding="utf-8")))
        for py in _source_files(under=root)
    ]


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def test_the_app_reaches_an_act_only_through_application() -> None:
    """No app module calls or imports a name that acts. **Replaced, not deleted.**

    The property is unchanged and the instrument is not: this reads the syntax tree, so the
    sentence in `room_bid.py` explaining the rule is a sentence, and `JournalRow.max_cap` is
    a field. The textual version could tell neither from a call.

    An import is banned alongside the call because a name bound but not yet used is the
    commit before the one that uses it.
    """
    offenders: list[tuple[str, str]] = []
    for where, tree in _app_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in ACTING_NAMES:
                offenders.append((where, f"calls {_called_name(node)}()"))
            elif isinstance(node, ast.ImportFrom):
                offenders += [
                    (where, f"imports {alias.name}")
                    for alias in node.names
                    if alias.name in ACTING_NAMES
                ]
    assert offenders == [], f"the app wires an action it must not take: {offenders}"


#: Where an arming *intent* may come from, spelled exactly. A name or attribute called
#: anything else is not an intent, it is a coincidence.
ARM_INTENT = ("arm", "armed")

#: The package prefix where an arming intent arrives as an HTTP request rather than as a
#: keystroke. `application/arming`'s own distinction: *"the per-invocation lock is a `--arm`
#: flag in a terminal and a body field in a request"*. A terminal flag **is** the second lock
#: — the operator typed it, now, for this run. A request field is not: a page can be
#: reloaded, restored by the session manager, or left open overnight, so the field has to be
#: combined with the ambient lock and the answer reported. That is what `decide_arming` is.
REQUEST_SURFACE = "api/"


def _on_request_surface(where: str) -> bool:
    """Is this module part of the request surface, on **any** platform?

    A predicate rather than a bare `where.startswith(REQUEST_SURFACE)`, because that
    comparison is a separator assumption and the separator is not the same everywhere.
    `_app_trees` used `str(py.relative_to(root))`, which on Windows is
    `api\\v1\\endpoints\\room_bid.py` — so the prefix never matched, and the two
    consumers failed in opposite directions:

    * `test_the_arming_contract_is_actually_exercised` went **red**, saying no route arms a
      child. Loud, and correct: that is the meta-guard, and it caught this.
    * `test_a_request_that_arms_asks_decide_arming_and_a_keystroke_does_not_have_to` went
      **green over nothing** — its loop `continue`s on every file, so `offenders` stays
      empty and the arming contract on the request surface was unchecked on Windows. Silent,
      and exactly the failure this file's own docstrings are about: a guard over a feature
      nobody scanned reassures.

    This file already knew: the `/api/` filter below carried a `.replace("\\", "/")` and
    was later retired in favour of `as_posix()`. Three sites kept the bare `str`.
    """
    return where.replace("\\", "/").startswith(REQUEST_SURFACE)


def _arm_writes(fn: ast.FunctionDef) -> list[ast.Constant]:
    """Every place this body *writes* `--arm` into an argv.

    A `Compare` is a read — `schedule.status` asks `"--arm" in argv` to report whether the
    installed job is armed, which is the opposite of arming one. Banning it would make the
    only command that can *tell* an operator their job is armed the command that fails this
    test, which is how the first acting guard came to ban the viewer and permit the bidder.
    """
    reads = {
        node
        for compare in ast.walk(fn)
        if isinstance(compare, ast.Compare)
        for node in ast.walk(compare)
    }
    return [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and node.value == ARM_FLAG and node not in reads
    ]


def _intent_names(test: ast.expr) -> set[str]:
    """The names and attributes an `If` test reads, by their last component."""
    found: set[str] = set()
    for node in ast.walk(test):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def test_nothing_arms_a_child_outside_an_arming_intent() -> None:
    """**The successor property, and the one worth having**: every path by which this app can
    cause an act is reached only through an arming intent somebody stated.

    That is stronger than "the app does not bid", and — unlike absence — it is still true
    once the app does. A hand-written `argv.append("--arm")` fails here; so does one guarded
    by a condition that reads anything other than an intent, which is what a refactor that
    moved the flag under `if league:` would look like.

    The intent may be a keystroke or a request, and the two are not held to the same bar:
    see :data:`REQUEST_SURFACE` and the test below it.
    """
    offenders: list[str] = []
    for where, tree in _app_trees():
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            writes = _arm_writes(fn)
            if not writes:
                continue
            guarded = [
                branch
                for branch in ast.walk(fn)
                if isinstance(branch, ast.If)
                and _intent_names(branch.test) & set(ARM_INTENT)
                and any(write in ast.walk(branch) for write in writes)
            ]
            if not guarded:
                offenders.append(f"{where}:{fn.name} arms without reading an arming intent")
    assert offenders == [], f"the arming contract is bypassed: {offenders}"


def test_a_request_that_arms_asks_decide_arming_and_a_keystroke_does_not_have_to() -> None:
    """The second tier, and the distinction is `application/arming`'s own.

    A `--arm` an operator typed **is** the second lock: they are at the keyboard, now, for
    this run. A request's `arm` field is not — the browser that sent it can be reloaded,
    restored or left open overnight — so it has to be combined with the ambient lock, and the
    answer has to name *every* shut lock rather than the first. Only `decide_arming` does
    both, which is why it is required on one surface and not on the other.
    """
    offenders: list[str] = []
    for where, tree in _app_trees():
        if not _on_request_surface(where):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or not _arm_writes(fn):
                continue
            asked = any(
                isinstance(node, ast.Call) and _called_name(node) == "decide_arming"
                for node in ast.walk(fn)
            )
            if not asked:
                offenders.append(
                    f"{where}:{fn.name} arms from a request without asking `decide_arming`"
                )
    assert offenders == [], f"a request armed on its own word: {offenders}"


def test_the_arming_decision_is_the_requests_and_never_a_constant() -> None:
    """`arm` comes from the body of the request that could act, restated every time.

    `application/arming`'s rule: a page can be reloaded, restored by the session manager, or
    left open overnight, and none of those may carry an arming decision forward. A route
    passing `arm=True` — or `arm=False` — would be making that decision for the operator, in
    the direction its author happened to pick.
    """
    offenders: list[str] = []
    for where, tree in _app_trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _called_name(node) in ARMING_NAMES):
                continue
            passed = [kw for kw in node.keywords if kw.arg == "arm"]
            if not passed:
                offenders.append(f"{where}: {_called_name(node)}() states no `arm`")
                continue
            for keyword in passed:
                if isinstance(keyword.value, ast.Constant):
                    offenders.append(
                        f"{where}: {_called_name(node)}(arm={keyword.value.value!r}) is a "
                        "decision made for the operator"
                    )
    assert offenders == [], f"an arming decision was hard-coded: {offenders}"


def test_the_arming_contract_is_actually_exercised() -> None:
    """A guard over a feature nobody wrote is worse than none: it reassures.

    The two tests above pass vacuously over an app that arms nothing — which is exactly what
    this app was until 3.9b. So the route that arms has to exist, and be found by the same
    walk the guards use rather than by a path written down here.
    """
    arming = [
        where
        for where, tree in _app_trees()
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and _arm_writes(fn)
    ]
    from_a_request = [where for where in arming if _on_request_surface(where)]
    deciding = [
        where
        for where, tree in _app_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) in ARMING_NAMES
    ]
    assert arming, "no app module arms a child: the guard above is scanning nothing"
    assert from_a_request, (
        "no route arms a child, so the `decide_arming` tier is scanning nothing — which is "
        "exactly what this app was until 3.9b, with both guards green"
    )
    assert deciding, "no app module asks `decide_arming`: the contract is unexercised"


def test_the_boundary_names_the_functions_that_actually_act() -> None:
    """A guard that misses the write path is worse than none — it reassures.

    This read the file's own *text* and so could not fail: `for acting in ("place_raise(",
    ...)` put those literals in the source it then searched, so the assertion found its own
    loop header whatever the ban contained. It reads :data:`ACTING_NAMES` — the object the
    scan uses — and the literals here are the expectation, which is the point of a meta-guard.

    The trailing `(` is gone with the textual scan: these are **names** now, matched against
    a call's callee, so `place_raise(` would match nothing and the ban would be empty.
    """
    must_be_banned = (
        "place_raise",  # PATCHes a raise to the RTDB — the one that spends credits
        "run_bid_loop",  # the loop that calls both, forever
        "decide_bid",  # chooses a raise
        "RoomTracker",  # owns the loop and the per-cycle decision
        "teamLineup_submit",  # POSTs the weekly lineup
    )
    missing = [name for name in must_be_banned if name not in ACTING_NAMES]

    assert missing == [], f"dropped from the acting boundary: {missing}"
    # Every banned name pinned, not a chosen few. `RoomTracker` was on the ban and off this
    # list, so it could be dropped silently.
    assert set(must_be_banned) == set(ACTING_NAMES), (
        f"the ban and its pin disagree: {set(ACTING_NAMES) ^ set(must_be_banned)}"
    )
    assert set(ARMING_NAMES) == {"decide_arming", "submit_lineup"}, (
        f"the arming boundary moved: {set(ARMING_NAMES) ^ {'decide_arming', 'submit_lineup'}}"
    )
    # Each of the three below is a way to make the guards above pass over nothing: an empty
    # intent set matches no `If` test, a `REQUEST_SURFACE` matching no file skips every
    # route, and a flag spelled anything else is a literal nothing writes.
    assert set(ARM_INTENT) == {"arm", "armed"}, f"the intent vocabulary moved: {ARM_INTENT}"
    assert REQUEST_SURFACE == "api/", f"the request surface moved: {REQUEST_SURFACE!r}"

    # The separator is part of the contract, and the only platform that disagrees is the
    # one nobody here develops on — `app-ci`'s Windows job is the sole evidence, which is
    # why this is asserted rather than left to it.
    assert _on_request_surface("api/v1/endpoints/room_bid.py")
    assert _on_request_surface("api\\v1\\endpoints\\room_bid.py"), (
        "the request-surface check is a separator assumption: on Windows it matches nothing, "
        "which fails one guard loudly and leaves its sibling scanning an empty set"
    )
    assert not _on_request_surface("cli.py")
    assert not _on_request_surface("infrastructure/jobs.py")

    # And the walk must hand it paths that check out on either platform. Asserted from the
    # **source**, not from a run: on macOS and Linux `str(path)` and `path.as_posix()`
    # return the same string, so a runtime check cannot tell the two apart and the one that
    # breaks Windows passes here. That is the same shape as the `--format` defaults on the
    # CLI side, pinned the same way.
    import inspect

    walk = inspect.getsource(_app_trees)

    assert "as_posix()" in walk, "`_app_trees` stopped normalising its separators"
    assert "str(py.relative_to" not in walk, (
        "`_app_trees` is building a native-separator path again; on Windows it matches no "
        "prefix, which fails one guard and silently empties another"
    )
    assert ARM_FLAG == "--arm", "a lock spelled any other way is one edit from open"


CAP_NAMES = (
    "max_cap",  # the guard inside `run_bid_loop`, between a walk-away and an unfieldable rosa
    "max_bid",  # what computes it from the band: credits left, less one per slot still owed
)
"""The cap, by the names that compute and apply it. A module-level tuple, not a local, so
the pin below can read the **object** the scan uses instead of the file that defines it —
`ACTING_NAMES`'s own lesson, learned the same way: emptying the list left every assertion
green, which is a guard reporting on nothing and reassuring while it does."""


def test_the_cap_guard_names_what_actually_computes_a_ceiling() -> None:
    """A guard over an empty list passes for ever. This is what makes the scan mean something.

    Survivor 13 of this slice's battery: replacing `CAP_NAMES` with `set()` left the app free
    to compute its own ceiling with the whole suite green.
    """
    assert set(CAP_NAMES) == {"max_cap", "max_bid"}, (
        f"the cap boundary moved: {set(CAP_NAMES) ^ {'max_cap', 'max_bid'}}"
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
    acting = set(CAP_NAMES)
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


def test_no_api_test_redirects_the_home_by_hand() -> None:
    """`Path.home()` is redirected in exactly one place, and the reason is a platform split.

    `posixpath.expanduser` reads `HOME`; `ntpath.expanduser` reads `USERPROFILE` and
    ignores `HOME`. A test that sets one of them redirects nothing on the other platform —
    it reads the operator's real home, finds nothing there, and fails with a number instead
    of an explanation. That was app-ci's last Windows failure.

    Why a source scan and not a behavioural test: the hand-rolled version **passes** on
    macOS and Linux, so nothing here can fail on it. A mutation restoring
    `monkeypatch.setenv("HOME", ...)` survived the battery for exactly that reason — the
    same shape as `_app_trees`' separator, where only the source can tell the right version
    from the one that breaks a platform nobody here runs.
    """
    import fantabot_app

    api_tests = Path(fantabot_app.__file__).parent / "api" / "tests"
    offenders = [
        f"{py.relative_to(api_tests).as_posix()}:{n}"
        for py in sorted(api_tests.rglob("test_*.py"))
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), start=1)
        if 'setenv("HOME"' in line or 'setenv("USERPROFILE"' in line
    ]

    assert offenders == [], (
        f"a test redirects the home by hand: {offenders}. Use `redirect_home` from "
        "`conftest`, which sets both variables — neither one alone covers both platforms."
    )
