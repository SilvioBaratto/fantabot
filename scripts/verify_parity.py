"""Every success criterion of the parity phase, as a measurement.

The spec is `SPEC.md` §12 while this phase is in flight, and `tasks/archive/parity-spec.md`
once it closes. Run it: `python scripts/verify_parity.py`. It prints what it measured against
what was expected and exits non-zero if any live check fails.

**Why this is a script and not a list of greps in a document.** §12 lists seventeen criteria
and nothing produced them — each was a sentence somebody would have had to re-check by hand,
which is the same as not having it. `scripts/verify_criteria.py` is the precedent and its
lesson is the reason the structural checks here ask the import graph and the syntax tree:
*a substring check cannot tell a call from a sentence about a call*. It flagged
`asta_planner.py` for a docstring saying nothing in that package may import typer, and
`fantalab_store.py` for a docstring explaining what a test forbids. This phase paid for the
same lesson twice more, on `max_cap`: once on a rule written down in a docstring, once on
`JournalRow.max_cap` — the *recorded* cap a viewer renders, which is the opposite of applying
one.

**Two suites, two interpreters.** The CLI's tests run under the conda `fanta` environment and
the app's under `app/.venv` through `uv`. Neither can import the other's dependencies, so the
app checks shell out rather than importing `fantabot_app` here.

**What this cannot check, and names instead.** Criterion 1 is a GitHub Actions matrix: the
path filter is a file and is measured, the three green runs are not reproducible on this
machine and are pointed at. Criterion 5's *"the CLI's own tests are unchanged by every lift"*
is a property of the diff, not of the tree — it is named, with the two places this phase
recorded an exception and why.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "fantabot"
APP = ROOT / "app"
APP_PACKAGE = APP / "fantabot_app"
FRONTEND = APP / "frontend" / "src" / "app"
for _helpers in (ROOT / "tests", ROOT / "tests" / "domain" / "asta"):
    sys.path.insert(0, str(_helpers))

failures: list[str] = []


def check(number: str, claim: str, measured: object, expected: object) -> None:
    ok = measured == expected
    if not ok:
        failures.append(f"SC {number}: {claim} -- measured {measured!r}, expected {expected!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} SC {number:<4} {claim}: {measured!r}")


def note(number: str, claim: str) -> None:
    print(f"  --   SC {number:<4} {claim}")


def _sources(under: Path) -> list[Path]:
    return [p for p in sorted(under.rglob("*.py")) if "__pycache__" not in p.parts]


def _trees(under: Path) -> list[tuple[str, ast.Module]]:
    return [
        (str(p.relative_to(under)), ast.parse(p.read_text(encoding="utf-8")))
        for p in _sources(under)
    ]


def _called(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _calls_to(under: Path, name: str) -> list[ast.Call]:
    return [
        node
        for _, tree in _trees(under)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called(node) == name
    ]


def _suite(args: list[str], *, cwd: Path = ROOT, uv: bool = False) -> str:
    command = (["uv", "run"] if uv else [sys.executable, "-m"]) + ["pytest", "-q", *args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode == 0:
        return "green"
    failed = [ln for ln in result.stdout.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    return "RED: " + "; ".join(failed[:4]) if failed else "RED"


def _exit(command: list[str], *, cwd: Path = ROOT) -> int:
    return subprocess.run(command, cwd=cwd, capture_output=True).returncode


def _served_paths() -> set[str]:
    """Every path the app actually serves, read from its own OpenAPI document.

    The app's route table rather than a list written here, and read through `uv` because
    `fantabot_app` is not importable from this interpreter. `api_router.routes` was the first
    attempt and is wrong: an included router's entries carry no `path` until the application
    is assembled, so it reported eighteen empty strings — one per `include_router` — which
    looks enough like a count to be believed.
    """
    script = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from fantabot_app.api.main import app\n"
        "spec = TestClient(app).get('/openapi.json').json()\n"
        "print('PATHS' + json.dumps(sorted(\n"
        "    f'{m.upper()} {p}' for p, ops in spec['paths'].items() for m in ops\n"
        ")))\n"
    )
    out = subprocess.run(
        ["uv", "run", "python", "-c", script], cwd=APP, capture_output=True, text=True
    ).stdout
    marker = out.rfind("PATHS")
    return set(json.loads(out[marker + 5 :])) if marker >= 0 else set()


#: Every `fantabot` command, and the app route that reaches it — or the reason there is none.
#:
#: A written table, because the join cannot be derived: `db price` is `POST
#: /asta/target-prices` and `lega sync` is `POST /actions/lega-sync`, and no naming rule
#: relates either pair. What the table is *checked against* is derived on both sides — the
#: live Typer tree and the live OpenAPI document — so a command added without a route, or a
#: route renamed under a command, fails here rather than in an operator's evening.
COVERAGE: dict[str, str] = {
    "asta bench": "",
    "asta bid": "POST /api/v1/asta/room/bid",
    "asta calibrate": "",
    "asta legality": "GET /api/v1/asta/legality",
    "asta live": "GET /api/v1/asta/advisory",
    "asta optimize": "GET /api/v1/asta/plan",
    "asta room": "POST /api/v1/asta/room/watch",
    "auth fantalab-login": "POST /api/v1/auth/fantalab-login",
    "auth forget": "DELETE /api/v1/auth/league/{league_id}",
    "auth login": "POST /api/v1/auth/login",
    "auth status": "GET /api/v1/auth/status",
    "config-check": "GET /api/v1/system/config",
    "db backfill-teams": "POST /api/v1/db/backfill-teams",
    "db check": "GET /api/v1/db/health",
    "db dump": "POST /api/v1/db/dump",
    "db exclude": "POST /api/v1/db/exclusions",
    "db exclusions": "GET /api/v1/db/exclusions",
    "db price": "POST /api/v1/asta/target-prices",
    "db scrape": "POST /api/v1/db/scrape",
    "db snapshot-team": "POST /api/v1/db/snapshot-team",
    "db unexclude": "DELETE /api/v1/db/exclusions/{player_id}",
    "harvest backfill": "POST /api/v1/harvest/backfill",
    "harvest collect": "POST /api/v1/harvest/collect",
    "harvest load": "POST /api/v1/harvest/load",
    "harvest scan": "POST /api/v1/actions/harvest-scan",
    "lega show": "GET /api/v1/lega",
    "lega sync": "POST /api/v1/actions/lega-sync",
    "lineup plan": "GET /api/v1/lineup/plan",
    "lineup show": "GET /api/v1/lineup/current",
    "lineup submit": "POST /api/v1/lineup/submit",
    "mantra-grid": "",
    "news fetch": "POST /api/v1/actions/news-fetch",
}

#: The commands with no route, and why. §12 expected exactly one entry; there are three, and
#: the other two are the same kind of thing `SPEC.md` T20 already rules CLI-only.
CLI_ONLY: dict[str, str] = {
    "mantra-grid": "T27 — a one-off collection into package data; nothing reads it per run",
    "asta bench": "developer machinery: replays a recorded JSONL a browser cannot hand over",
    "asta calibrate": "developer machinery: a minutes-long sweep over 45 recorded rooms",
}


def _cli_commands() -> set[str]:
    """The live Typer tree, flattened — not a list written here."""
    import click
    from typer.main import get_command

    from fantabot.interface.app import app as cli

    def leaves(command: click.Command, prefix: tuple[str, ...] = ()) -> set[str]:
        subcommands = getattr(command, "commands", None)
        if not subcommands:
            return {" ".join(prefix)}
        return {
            leaf
            for name, sub in subcommands.items()
            for leaf in leaves(sub, (*prefix, name))
        }

    return {leaf for leaf in leaves(get_command(cli)) if leaf}


def main() -> int:
    print("\n1 — the floor")
    workflow = (ROOT / ".github" / "workflows" / "app-ci.yml").read_text(encoding="utf-8")
    check("1", "app-ci watches the lock the Windows job exercises",
          "src/fantabot/adapters/files/lock.py" in workflow, True)
    note("1b", "three green OS runs are the matrix's own record, not reproducible here")

    print("\n2 — the stop, on both platforms")
    stop_file = APP_PACKAGE / "api" / "tests" / "test_processes.py"
    [proof] = [
        node
        for node in ast.walk(ast.parse(stop_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_the_first_stop_disarms_and_the_second_exits"
    ]
    # Read as syntax, and it earned that on its first run: the docstring **names** the
    # criterion — *"proven by a test that does not reference `signal.SIGKILL`"* — so a
    # substring check reported the proof as failing because it says what it proves. The
    # docstring is dropped and what is left is code.
    code = ast.Module(body=proof.body[1:], type_ignores=[])
    check("2", "the two-stage proof names no SIGKILL",
          [n for n in ast.walk(code)
           if (isinstance(n, ast.Attribute) and n.attr == "SIGKILL")
           or (isinstance(n, ast.Name) and n.id == "SIGKILL")
           or (isinstance(n, ast.Constant) and n.value == "SIGKILL")], [])
    check("2b", "both live commands poll the flag",
          _suite(["tests/interface/test_asta_live_stop.py"]), "green")

    print("\n3 — the record outside the tree")
    tasks = ROOT / "tasks"
    check("3", "tasks/ is a symlink to the home directory",
          tasks.is_symlink() and str(tasks.resolve()).endswith("/.fantabot/tasks"), True)
    check("3b", "git status is clean with the record in place",
          subprocess.run(["git", "status", "--porcelain", "tasks"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip(), "")
    # **The corrected form** (0.1). The first wording was "`git clean -xfd` leaves it
    # untouched", which is not what happens and never was: git removes the *symlink*, which
    # is an untracked entry in the working tree. What matters is the record, and git cannot
    # reach it — the proof is that clean lists the link as one entry and never anything
    # beneath it. At worst the phase costs one `ln -s`.
    cleaned = subprocess.run(["git", "clean", "-xfdn"], cwd=ROOT,
                             capture_output=True, text=True).stdout.splitlines()
    check("3c", "git clean names the link and never what is under it",
          sorted(ln for ln in cleaned if "tasks" in ln), ["Would remove tasks"])
    check("3d", "and the record itself is outside the repository",
          str(tasks.resolve()).startswith(str(ROOT)), False)

    print("\n4 — the dead ORM scaffold")
    api = APP_PACKAGE / "api"
    check("4", "api/infrastructure/orm/ and api/schemas/ are gone",
          [d for d in ("infrastructure/orm", "schemas") if (api / d).exists()], [])
    check("4b", "no engine under api/, tests included",
          [str(p.relative_to(api)) for p in _sources(api)
           if "create_engine(" in p.read_text(encoding="utf-8")], [])

    print("\n5 — both suites")
    check("5", "the CLI suite", _suite(["tests/"]), "green")
    check("5b", "the app suite", _suite([], cwd=APP, uv=True), "green")
    note("5c", "'the CLI's tests unchanged by every lift' is a property of the diff. Two "
               "exceptions are recorded: 3.9a repointed one monkeypatch (assertions "
               "byte-identical) and 3.11 replaced the acting guard, which is T21's own task")

    print("\n6 — parity")
    check("6", "the parity tier", _suite(["-m", "parity"], cwd=APP, uv=True), "green")
    parity = APP_PACKAGE / "api" / "tests" / "parity"
    check("6b", "it covers the four named pairs",
          sorted(p.stem.removeprefix("test_parity_") for p in parity.glob("test_parity_*.py")
                 if p.stem.removeprefix("test_parity_") in
                 {"asta_plan", "lineup", "pricing", "lega"}),
          ["asta_plan", "lega", "lineup", "pricing"])
    check("6c", "no parity test builds an engine of its own",
          [str(p.relative_to(parity)) for p in _sources(parity)
           if "create_engine(" in p.read_text(encoding="utf-8")], [])

    print("\n7 — the layers")
    layers = (ROOT / "tests" / "test_layers.py").read_text(encoding="utf-8")
    check("7", "the T-spine rule exists", "EXPECTED_WRITING_VIOLATIONS" in layers, True)
    from test_layers import EXPECTED_WRITING_VIOLATIONS  # type: ignore[import-not-found]

    check("7b", "its ratchet is empty", sorted(EXPECTED_WRITING_VIOLATIONS), [])

    print("\n8 — the clock")
    check("8", "one calendar seam per surface", _suite(["tests/domain/asta/test_asta_clock.py"]),
          "green")
    from test_asta_clock import SURFACES  # type: ignore[import-not-found]

    check("8b", "and the app is among the surfaces",
          sorted(s.name for s in SURFACES if s.name.startswith("app.")),
          ["app.asta", "app.lineup"])
    check("8c", "each records exactly one", sorted({s.seams for s in SURFACES}), [1])

    print("\n9 — the acting guard")
    fitness = (APP / "tests" / "test_fitness.py").read_text(encoding="utf-8")
    check("9", "the arming contract replaced it",
          "test_nothing_arms_a_child_outside_an_arming_intent" in fitness, True)
    check("9b", "and nothing scans the app's text for an acting name",
          "for term in ACTING_NAMES" in fitness, False)
    check("9c", "it is exercised", _suite(["tests/test_fitness.py"], cwd=APP, uv=True), "green")

    print("\n10 — the pinned outcomes")
    check("10", "three routes, three pinned tuples",
          _suite(["fantabot_app/api/tests/test_outcomes.py"], cwd=APP, uv=True), "green")
    degraded = [
        f"{where}:{node.lineno}"
        for where, tree in _trees(APP_PACKAGE)
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "Exception"
        and any(
            isinstance(inner, ast.keyword) and inner.arg == "found"
            and isinstance(inner.value, ast.Constant) and inner.value.value is False
            for inner in ast.walk(node)
        )
    ]
    check("10b", "`except Exception -> found=False` appears nowhere", degraded, [])

    print("\n11 — the corpus shape")
    reads = _calls_to(PACKAGE, "read_plan_inputs")
    check("11", "every read_plan_inputs states the shape",
          [ast.unparse(c)[:60] for c in reads
           if {"num_teams", "num_credits"} - {k.arg for k in c.keywords}], [])
    note("11b", f"six call sites when §12 was written, {len(reads)} now — `asta live`'s moved "
                "into `application/asta_advisory` with 3.10's lift")
    check("11c", "an unrecorded shape raises rather than returning nothing",
          _suite(["tests/application/test_corpus_shape.py"]), "green")

    print("\n12 — the journal row, defined once")
    # `ast.AnnAssign` as well as `ast.Assign`: `ROW_FIELDS: frozenset[str] = frozenset(...)`
    # is an annotated assignment and the first version of this check saw none at all, which
    # reported "defined in zero modules" as a failure of the code rather than of the scan.
    row_fields = [
        where for where, tree in _trees(PACKAGE)
        for node in ast.walk(tree)
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "ROW_FIELDS" for t in node.targets))
        or (isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name) and node.target.id == "ROW_FIELDS")
    ]
    check("12", "ROW_FIELDS is defined in exactly one module",
          row_fields, ["adapters/files/room_journal.py"])
    endpoint = (api / "v1" / "endpoints" / "asta.py").read_text(encoding="utf-8")
    check("12b", "the endpoint renders the three fields that were missing",
          [f for f in ("bargain_spent", "bargain_allowance", "error") if f not in endpoint], [])
    # Structural, and it earned that immediately: `endpoints/asta.py`'s module docstring
    # *names* the journal — "it also serves the room journal, `data/room_journal.jsonl`" —
    # which is documentation, not a path being joined. What the criterion forbids is a module
    # building the path instead of calling `config.journal_path()`, so the scan looks for the
    # literal outside a docstring.
    def _docstrings(tree: ast.Module) -> set[int]:
        holders = [tree, *(n for n in ast.walk(tree)
                           if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef))]
        return {
            id(h.body[0].value)
            for h in holders
            if h.body and isinstance(h.body[0], ast.Expr)
            and isinstance(h.body[0].value, ast.Constant)
        }

    # The app's own modules, not its tests: a fixture that writes a journal has to name the
    # file it writes, and banning that would make the suite that proves the reader works the
    # thing that fails. What the criterion forbids is a *module* joining the path.
    hand_rolled = [
        f"{where}:{node.lineno}"
        for where, tree in _trees(APP_PACKAGE)
        if "tests" not in Path(where).parts
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "room_journal.jsonl" in node.value
        and id(node) not in _docstrings(tree)
    ]
    check("12c", "no app module joins the journal path by hand", hand_rolled, [])

    print("\n13 — the walk-away, beside its price")
    page = (FRONTEND / "pages" / "asta" / "asta.html").read_text(encoding="utf-8")
    check("13", "the page renders a walk-away and its provenance, separately",
          all(term in page for term in ("walk_away", "walk_away_provenance")), True)

    print("\n14 — every command reachable, or named")
    commands = _cli_commands()
    served = _served_paths()
    check("14", "the coverage table names every command",
          sorted(commands - COVERAGE.keys()), [])
    check("14b", "and names no command that does not exist",
          sorted(COVERAGE.keys() - commands), [])
    check("14c", "every route it names is served",
          sorted({r for r in COVERAGE.values() if r and r not in served}), [])
    check("14d", "every command with no route has a stated reason",
          sorted({c for c, r in COVERAGE.items() if not r} - CLI_ONLY.keys()), [])
    for command, why in sorted(CLI_ONLY.items()):
        note("14e", f"{command}: {why}")

    print("\n15 — the two deliberate divergences, on the screen")
    harvest_page = (FRONTEND / "pages" / "harvest" / "harvest.html").read_text(encoding="utf-8")
    # The dialog, not the page. The first version read `accounts.html` for the word "purge"
    # and reported the divergence as unsaid — it is said, in the one screen where somebody is
    # about to perform it, which is where T39 asks for it. A criterion measured against the
    # wrong file fails in the direction that invents work.
    dialog = (FRONTEND / "pages" / "accounts" / "disconnect-dialog.html").read_text(
        encoding="utf-8"
    )
    check("15", "(a) the starving pool is refused, and the page says so",
          all(term in harvest_page.lower() for term in ("pool", "refus")), True)
    check("15b", "(b) the disconnect names the six tables it clears",
          [t for t in ("league_snapshot", "league_team_snapshot", "league_player_pool",
                       "league_custom_role", "league_competition", "league_fixture")
           if t not in dialog], [])
    check("15c", "and says `auth forget` is not the same act",
          "auth forget" in dialog and "token row alone" in dialog, True)

    print("\n16 — lint and types, both trees")
    check("16", "ruff on src and tests", _exit(["ruff", "check", "src", "tests"]), 0)
    check("16b", "mypy on src", _exit(["mypy"]), 0)
    check("16c", "ruff on the app",
          _exit(["uv", "run", "ruff", "check", "fantabot_app", "tests"], cwd=APP), 0)
    check("16d", "mypy on the app", _exit(["uv", "run", "mypy", "fantabot_app"], cwd=APP), 0)

    print("\n17 — no path that resolves from the wrong directory")
    env_files = [p for p in (ROOT / ".env", ROOT / ".env.example", APP / ".env") if p.exists()]
    check("17", "no FANTABOT_DATABASE_URL in any .env",
          [p.name for p in env_files
           if any(line.strip().startswith("FANTABOT_DATABASE_URL=")
                  for line in p.read_text(encoding="utf-8").splitlines())], [])
    # A ratchet, not a bare `== []`, and the two entries are the phase's own record rather
    # than an exemption invented here. Both are deliberate and both are written down:
    #
    #   `config.fantabot_data_dir` is the journal's parent, and `CLAUDE.md` refuses to move
    #   it — the journal is an artefact the CLI owns and the 2026-09-01 audit was done
    #   against it. The screen says which file it read instead of implying there is only one.
    #
    #   `cli.LEGACY_HARVEST_DIR` is where the artefacts were *before* the home existed. It is
    #   the default source of `harvest adopt`, a one-time move, and naming a harvest path
    #   explicitly resolving against the cwd is stated behaviour, not a leak.
    #
    # Anything else fails, which is the half of the criterion that is actually live.
    EXPECTED_RELATIVE = {"config.py", "cli.py"}
    relative = {
        Path(where).name
        for under in (PACKAGE, APP_PACKAGE)
        for where, tree in _trees(under)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("./data/", "data/aste_live"))
    }
    check("17b", "no new relative harvest or journal path in either package",
          sorted(relative - EXPECTED_RELATIVE), [])
    check("17c", "and the two recorded ones are still the only two",
          sorted(relative), sorted(EXPECTED_RELATIVE))
    note("17d", "config.fantabot_data_dir is the journal's parent, deliberately not moved "
                "(CLAUDE.md); cli.LEGACY_HARVEST_DIR is `harvest adopt`'s source, pre-home")

    print()
    for line in failures:
        print(f"  {line}")
    print(f"\n{'ALL LIVE CRITERIA PASS' if not failures else f'{len(failures)} FAILED'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
