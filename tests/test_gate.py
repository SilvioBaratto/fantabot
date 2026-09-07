"""The standing gate is runnable, and says nothing it cannot back up.

**Why this exists.** `scripts/gate.sh` is the one command the repository asks you to run
before a commit, and it is checked by nothing -- it is shell, so `ruff`, `mypy` and
`pytest` all step over it. That is how it came to be red from the repository root while
still being cited as the gate: its first line was a bare `python -m pytest -q`, the root
`pyproject.toml` sets no `testpaths`, so a root run collects `app/fantabot_app/api/tests`
and `app/tests/test_server.py`. Those import fastapi, which the conda `fanta` env does
not have -- the app lives in its own uv venv at `app/.venv`. Collection errored before a
single test ran and the gate could not be executed as written.

Two properties are pinned here, both of them the gate's own claims about itself.

**Every pytest invocation names a path.** Not a style rule: an unscoped run from the root
collects the app tree, which is a different interpreter's. `SPEC.md` 7.1 records this as
expected behaviour of a root `pytest`; the gate has to be the thing that is scoped.

**Nothing is piped.** The gate's docstring says so, and gives the reason: a pipeline's
exit status is its *last* command's, so `pytest | tail -1` exits 0 with a failing suite.
That masked a real failure three times, once past a commit. The claim is worth a test
precisely because the failure it describes is invisible -- a piped gate still prints
"GATE GREEN".
"""

from __future__ import annotations

import re

from _paths import REPO

GATE = REPO / "scripts" / "gate.sh"


def _command_lines() -> list[str]:
    """The gate's executable lines, with the `run "<label>"` prefix stripped.

    The label has to go before anything is matched against the command: the first entry
    is named `"unit tests"`, and a scan for the word `tests` finds it there and calls an
    unscoped invocation scoped.
    """
    lines = [
        line.strip()
        for line in GATE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [re.sub(r'^run\s+"[^"]*"\s*', "", line) for line in lines]


def test_every_pytest_invocation_is_scoped_to_a_path() -> None:
    unscoped = [
        line
        for line in _command_lines()
        if "pytest" in line and not re.search(r"(^|\s)tests(/\S*)?(\s|$)", line)
    ]
    assert unscoped == [], (
        "an unscoped `pytest` in the gate collects app/ from the repository root, "
        f"where fastapi is not installed: {unscoped}"
    )


def test_nothing_in_the_gate_is_piped() -> None:
    # `|| `, `|&` and the `${1:-}` default are not pipes; a single `|` between commands is.
    piped = [line for line in _command_lines() if re.search(r"(?<![|&])\|(?!\|)", line)]
    assert piped == [], (
        "a pipeline's exit status is its last command's, so a piped gate reports "
        f"GATE GREEN over a failing suite: {piped}"
    )
