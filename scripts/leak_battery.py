"""The leak battery: six mutations that let the future into a fit, and the tests that see them.

A backtest replays a database that already holds the answer, so a leak does not fail — it
*flatters*. The model looks better than it is, the gate passes, and nothing anywhere goes
red. The only defence is to introduce the leak on purpose and check that something notices.

Six mutants, each a way the replay could see giornata `g` while predicting it:

* **M1** the date cutoff `<` becomes `<=` — the giornata's own matches enter the fit;
* **M2** the cutoff becomes `giornata < g` — a postponed match keeps its number and is
  played weeks later, so this is M1 with a longer fuse and no obvious symptom;
* **M3** `qi` is swapped for `fvm`, and then for `qa` — the prior's covariate becomes an
  end-of-season number. ⚠ **Unrepresentable**: `history.Valuation` carries `qi` and
  `squadra` and nothing else (SPEC A9), so there is no `fvm` to swap in. That is a better
  answer than a killed mutant — the leak cannot be written — and it is asserted
  structurally by `test_history.py` rather than mutated here;
* **M4** the opponent pool includes g — the model is told what it is playing against;
* **M5** the fits use the whole season rather than the part before g;
* **M6** `qi` comes from 2026/27 in the replay, not from the replayed season.

Run it from the worktree root::

    .venv/bin/python scripts/leak_battery.py

**Commit before running.** A mutant is applied by rewriting a source file and reverted by
rewriting it back; `git checkout` would delete uncommitted work in the same file, which is
how a previous battery lost the code it was testing. The `__pycache__` purge on both edges
is not optional either: a same-length revert leaves the mutated bytecode in place and the
next run reads as flaky.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"
TIMEOUT = 600


@dataclass(frozen=True)
class Mutant:
    """One leak, where it lives, and the test that must see it."""

    name: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]


BACKTEST = "src/fantabot/application/lineup_backtest.py"
HISTORY = "src/fantabot/domain/lineup/history.py"
GATE_TESTS = (
    "tests/application/test_lineup_backtest.py",
    "tests/domain/lineup/test_history.py",
)

MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        name="M1 the date cutoff admits the giornata itself",
        file=HISTORY,
        old="    return [row for row in rows if row.fixture.played_on < cutoff]",
        new="    return [row for row in rows if row.fixture.played_on <= cutoff]",
        tests=GATE_TESTS,
    ),
    Mutant(
        name="M2 the cutoff becomes the giornata number",
        file=BACKTEST,
        old="    past = before(data.rows, cutoff)",
        new="    past = [row for row in data.rows if row.fixture.giornata < giornata]",
        tests=GATE_TESTS,
    ),
    Mutant(
        name="M3 the prior's covariate is an end-of-season number",
        file=HISTORY,
        # The nearest representable form of A9's leak: the covariate stops being the
        # preseason `qi` and becomes something the replayed season only knew at the end.
        # `Valuation` has no `fvm` to point at, so the leak is injected by *widening* it.
        old="    qi: int\n    squadra: str",
        new="    qi: int\n    squadra: str\n    fvm: int = 0",
        tests=GATE_TESTS,
    ),
    Mutant(
        name="M4 the opponent pool includes this giornata",
        file=BACKTEST,
        old="            baseline_scores[room.asta_id].extend(s.fantapunti for s in standings)",
        new=(
            "            opponent = _opponent_for(\n"
            "                [*baseline_scores[room.asta_id], *(s.fantapunti for s in standings)]\n"
            "            )\n"
            "            baseline_scores[room.asta_id].extend(s.fantapunti for s in standings)"
        ),
        tests=GATE_TESTS,
    ),
    Mutant(
        name="M5 the fits use the whole season",
        file=BACKTEST,
        old="    history_rows = observations(past, rules=rules, valuations=valuations)",
        new="    history_rows = observations(list(data.rows), rules=rules, valuations=valuations)",
        tests=GATE_TESTS,
    ),
    Mutant(
        name="M6 qi comes from the roles season, not the replayed one",
        file=BACKTEST,
        old="    valuations = history.valuations(season)",
        new="    valuations = history.valuations(roles_season)",
        tests=GATE_TESTS,
    ),
)


def purge() -> None:
    """Every `__pycache__` under the worktree. A same-length revert otherwise leaves the
    mutated bytecode running, and the next mutant reads as flaky."""
    for cached in ROOT.rglob("__pycache__"):
        if ".venv" not in str(cached) and "node_modules" not in str(cached):
            shutil.rmtree(cached, ignore_errors=True)


def run(tests: tuple[str, ...]) -> tuple[bool, str]:
    """The named tests. A hang counts as a failure: a mutant that stops the run dead has
    been noticed, and the guard in `domain/lineup/build.py`'s `place_all` did exactly that
    once. (That function was deleted on 2026-09-24, unused; the finding is stated here
    rather than cited, so it survives the code that produced it.)"""
    try:
        finished = subprocess.run(
            [str(PYTHON), "-m", "pytest", "-x", "-q", "--no-header",
             "-p", "no:cacheprovider", *tests],
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    return finished.returncode == 0, (finished.stdout or "")[-500:]


def dirty() -> bool:
    finished = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    )
    return bool(finished.stdout.strip())


def main() -> int:
    if dirty():
        print("refusing: the worktree is dirty. Commit first — a revert here rewrites files.")
        return 2
    purge()
    green, tail = run(GATE_TESTS)
    if not green:
        print(f"refusing: the baseline is already red.\n{tail}")
        return 2
    print("baseline green\n")

    survivors: list[str] = []
    for mutant in MUTANTS:
        path = ROOT / mutant.file
        original = path.read_text(encoding="utf-8")
        occurrences = original.count(mutant.old)
        if occurrences != 1:
            print(f"{mutant.name}: ANCHOR x{occurrences} — not applied")
            survivors.append(mutant.name)
            continue
        path.write_text(original.replace(mutant.old, mutant.new), encoding="utf-8")
        purge()
        passed, tail = run(mutant.tests)
        path.write_text(original, encoding="utf-8")
        purge()
        print(f"{mutant.name}: {'SURVIVED' if passed else 'killed'}")
        if passed:
            survivors.append(mutant.name)
            print(f"    {tail.replace(chr(10), ' ')[:200]}")

    green, tail = run(GATE_TESTS)
    print(f"\nbaseline after: {'green' if green else 'RED — a revert did not take'}")
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} killed")
    return 0 if not survivors and green else 1


if __name__ == "__main__":
    sys.exit(main())
