"""`app-ci` triggers on everything it runs, and its two path filters agree.

**Why this is a test and not a comment.** `app-ci` holds the repository's only Windows
runner, and the app supervises a `fantabot` child process — so the modules on that path
are exercised there and nowhere else. Its filter was `app/**` alone, which meant a change
to `adapters/files/lock.py`, the very file whose Windows backend was in question, never
triggered the one job that runs it. A path filter that has stopped covering what it
claims fails *silently*: the workflow does not run, and a workflow that does not run
looks exactly like a workflow that passed.

The second check is about the fix rather than the bug. GitHub Actions' parser does not
resolve YAML anchors, so `paths: *watched` would be read as a literal and match nothing —
the two lists have to be written out twice, and two lists that must agree are two lists
that drift.

Parsed textually because the `fantabot` environment has no `pyyaml`, and adding a
dependency so that a CI file can be read would be a poor trade.
"""

from __future__ import annotations

import re

from _paths import REPO

APP_CI = REPO / ".github" / "workflows" / "app-ci.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"

#: Every module the app spawns or supervises whose behaviour differs by operating system.
#: `app-ci` is the only place any of them meets Windows.
SUPERVISED = (
    "src/fantabot/adapters/files/lock.py",
    "src/fantabot/adapters/files/stopflag.py",
    "src/fantabot/interface/harvest.py",
    "src/fantabot/application/harvest_supervisor.py",
)


def _path_filters() -> list[list[str]]:
    """Each `paths:` block's entries, in file order. One per trigger."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in APP_CI.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*paths:\s*$", line):
            current = []
            blocks.append(current)
            continue
        if current is None:
            continue
        if (entry := re.match(r'^\s*-\s*"([^"]+)"\s*$', line)) is not None:
            current.append(entry.group(1))
        elif line.strip() and not line.strip().startswith("#"):
            current = None
    return blocks


def test_both_triggers_filter_on_the_same_paths() -> None:
    push, pull_request = _path_filters()

    assert push == pull_request, "the push and pull_request filters have drifted apart"


def test_every_os_specific_module_the_app_supervises_triggers_the_windows_job() -> None:
    push, _ = _path_filters()

    missing = [module for module in SUPERVISED if module not in push]
    assert missing == [], (
        "app-ci holds the only Windows runner, and these are not in its path filter, "
        f"so changing them runs no Windows job at all: {missing}"
    )


def test_the_filter_uses_no_yaml_anchor() -> None:
    """GitHub Actions does not resolve anchors: an aliased `paths:` parses fine locally
    and matches nothing there, which is the silent failure wearing the fix's clothes.

    Comment lines are skipped, because the file explains the rule in prose and a scan
    over the whole text finds the explanation and calls it the violation.
    """
    code = [
        line
        for line in APP_CI.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    anchored = [line for line in code if re.search(r":\s*[*&]\w", line)]
    assert anchored == [], f"GitHub Actions will not resolve these: {anchored}"


def test_it_cancels_a_superseded_run() -> None:
    """Three OSes plus a real Postgres provision. A push on top of a push left the first
    matrix running; `ci.yml` has had this block all along and this one did not."""
    source = APP_CI.read_text(encoding="utf-8")

    assert "concurrency:" in source
    assert "cancel-in-progress: true" in source


def test_the_fantabot_workflow_scopes_every_pytest_to_tests() -> None:
    """The same defect `scripts/gate.sh` had, in its other home — and it was live.

    `ci.yml` ran `pytest -q` and `pytest -q -m db` with no path. The root
    `pyproject.toml` sets no `testpaths` (deliberately: the `integration`/`e2e` marker
    declarations there exist *because* a root run collects `app/`), so both tiers
    collected `app/fantabot_app/api/tests` and `app/tests/test_server.py`, which import
    fastapi. The `fantabot` environment does not have it — the app has its own venv — so
    both jobs errored at collection with zero tests run, and had done for as long as the
    app has had tests.

    Scoped here rather than solved with `testpaths` for the same reason as in the gate:
    `testpaths` would silence the marker declarations that make a root run warning-free.
    The app's own suite is `cd app && uv run pytest`, and `app-ci` is where it runs.
    """
    # Only `run:` lines execute. A step's `name:` is prose and routinely says "pytest" —
    # the same trap the gate's own guard fell into, where the label "unit tests" matched
    # the scan for a path and made an unscoped invocation read as scoped.
    commands = [
        stripped.removeprefix("run:").strip()
        for line in CI.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()).startswith("run:")
    ]
    offenders = [
        command
        for command in commands
        if re.search(r"\bpytest\b", command)
        and not re.search(r"(^|\s)tests(/\S*)?(\s|$)", command)
    ]

    assert offenders == [], (
        "an unscoped pytest in ci.yml collects app/, where fastapi is not installed: "
        f"{offenders}"
    )
