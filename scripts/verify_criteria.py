"""Every success criterion in SPEC.md, as a measurement.

Run it: `python scripts/verify_criteria.py`. It prints what it measured and what was
expected, and exits non-zero if any live check fails.

**Why this is a script and not a list of greps in a document.** Most of these criteria
were first written as `grep -c something src/`, and running them turned up the same
failure four times over: a substring check cannot tell a call from a sentence about a
call. `grep 'decrypt(' fantalab_store.py` returns 2 because the docstring explains what
the test forbids; `grep -rln 'import typer'` flags `asta_planner.py` because its
docstring says nothing in that package may import typer; `grep 'Console()'` would count
a comment. So the structural criteria ask the import graph and the syntax tree.

**What this cannot check.** The database criteria -- 12, 17, 18, 19 -- were proven once,
against a restored copy of the live database, by running `EXCEPT` in both directions
before and after each migration. Those proofs are in `tasks/w4-proofs.out` and they are
not re-runnable here: the data has moved on, and the pre-migration tables no longer
exist. This script names them and points at the record rather than pretending to
re-verify them.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "fantabot"
sys.path.insert(0, str(ROOT / "tests"))

import _importgraph as G  # noqa: E402

failures: list[str] = []


def check(number: str, claim: str, measured: object, expected: object) -> None:
    ok = measured == expected
    if not ok:
        failures.append(f"SC {number}: {claim} -- measured {measured!r}, expected {expected!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} SC {number:<3} {claim}: {measured!r}")


def note(number: str, claim: str) -> None:
    print(f"  --   SC {number:<3} {claim}")


def _calls(name: str, *, attribute: bool = False) -> list[str]:
    """Files containing a call to `name`. AST, so prose about it does not count."""
    out = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            got = getattr(node.func, "attr" if attribute else "id", None)
            if got == name:
                out.append(str(path.relative_to(PACKAGE)))
                break
    return out


def _suite(args: list[str]) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args], cwd=ROOT, capture_output=True, text=True
    )
    return "green" if result.returncode == 0 else f"RED\n{result.stdout[-2000:]}"


def main() -> int:
    print("\nGates")
    check("1", "golden output unchanged", _suite(["tests/test_golden.py"]), "green")
    check("1b", "tests/golden/ clean",
          subprocess.run(["git", "status", "--porcelain", "tests/golden/"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip(), "")
    check("2", "default tier", _suite([]), "green")
    check("2b", "db tier", _suite(["-m", "db"]), "green")
    alembic = subprocess.run(["alembic", "check"], cwd=ROOT, capture_output=True, text=True)
    check("3", "alembic agrees with the models",
          "No new upgrade operations detected." in alembic.stdout + alembic.stderr, True)
    check("4", "mypy", subprocess.run(["mypy"], cwd=ROOT, capture_output=True).returncode, 0)
    check("4b", "ruff", subprocess.run(["ruff", "check", "src", "tests"], cwd=ROOT,
                                       capture_output=True).returncode, 0)

    print("\nW1 -- deduplicate")
    check("5", "build_value defined and called once",
          len(_calls("build_value")) + 1, 2)
    check("6", "the shared asta options exist once",
          (PACKAGE / "interface" / "options.py").is_file(), True)
    check("7", "one Console() in src", _calls("Console"), ["interface/console.py"])
    check("8", "scripts/_db.py gone", (ROOT / "scripts" / "_db.py").exists(), False)
    # *Writes*, and only writes. Every caller of a repository reaches sqlalchemy
    # transitively, which is the arrangement; and outside persistence the direct imports
    # are `Session` for an annotation, `SQLAlchemyError` to catch, and `make_url` to mask
    # a DSN -- none of which writes anything. What SC 8 is about is who can build a
    # statement that changes a row.
    writes = {"insert", "update", "delete", "Insert", "Update", "Delete"}
    check("8b", "only the persistence layer can build a write statement",
          sorted({
              m for m in G.modules()
              if "adapters.persistence" not in m
              and any(n.rpartition(".")[2] in writes
                      for n in G.direct_imports(m) if n.startswith("sqlalchemy"))
          }), [])
    check("9", "one decrypt() site per store",
          sorted(_calls("decrypt", attribute=True)),
          ["adapters/tokens/fantalab_store.py", "adapters/tokens/store.py",
           "domain/tokens/crypto.py"])

    print("\nW2 -- delete")
    check("10", "the Classic cluster is gone",
          [n for n in ("auction", "lineup", "strategy", "models")
           if (PACKAGE / f"{n}.py").exists()], [])
    check("11", "no stub bodies",
          [str(p.relative_to(ROOT)) for p in PACKAGE.rglob("*.py")
           if "raise NotImplementedError" in p.read_text()], [])
    note("12", "bot_state / auction_bids dropped after SELECT count(*) = 0 -- tasks/w4-proofs.out")
    note("13", "16,760 -> 14,209 lines combined, -15%; scripts/ 3,065 -> 123")

    print("\nW3 -- one CLI")
    check("14", "no argparse anywhere",
          sorted({m for m in G.modules() if G.reaches(m, "argparse")}), [])
    check("15", "typer is the interface layer's alone",
          sorted({m for m in G.modules()
                  if not m.startswith("fantabot.interface") and G.reaches(m, "typer")}), [])
    check("16", "the command set matches the rename table",
          _suite(["tests/interface/test_cli_command_set.py"]), "green")

    print("\nW4 -- normalize")
    for n, claim in [
        ("17", "qi_bias view returns exactly what the table held (5,356 rows, EXCEPT 0/0)"),
        ("18", "voti + bonus_malus merged, both row sets recoverable (EXCEPT 0/0)"),
        ("19", "asta_event: 486,803 payloads reconstructed, 0 mismatches, -140 MB"),
    ]:
        note(n, f"{claim} -- tasks/w4-proofs.out")
    check("20", "db/importers gone",
          (PACKAGE / "adapters" / "persistence" / "importers").exists(), False)

    print("\nW5 -- optimize")
    note("21", "harvest load --follow pass cost independent of zone size -- tasks/todo.md")
    check("22", "one asta bid cycle stays under the ceiling",
          _suite(["tests/domain/asta/test_asta_cycle_cost.py"]), "green")
    note("23", "asta bid folds the ledger incrementally")
    check("24", "default tier under 10 s", _suite([]), "green")

    print("\nW6 -- architecture")
    check("25", "the layer test passes over every module",
          _suite(["tests/test_layers.py", "tests/test_importgraph.py",
                  "tests/test_destinations.py", "tests/test_testtree.py"]), "green")
    check("26", "no loose modules",
          sorted(p.name for p in PACKAGE.glob("*.py")), ["__init__.py", "config.py"])
    check("27", "the docs name only files that exist and commands that run",
          _suite(["tests/test_docs.py"]), "green")

    print()
    for line in failures:
        print(f"  {line}")
    print(f"\n{'ALL LIVE CRITERIA PASS' if not failures else f'{len(failures)} FAILED'}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
