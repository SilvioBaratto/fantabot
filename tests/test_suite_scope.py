"""What a bare `pytest` from the repository root collects, and whose `conftest` it gets.

There are two `conftest.py` in this repository — `tests/` and `app/` — and neither tree is
a package. Under pytest's default `prepend` import mode a conftest is imported by its bare
basename, so both want the module name `conftest` and the first one imported keeps it.
`app` sorts before `tests`, so a root run that collects both trees hands
`from conftest import ...` the *app's* module.

What that broke: `tests/test_integration_isolation.py` imports `refuse_canonical` and
`CanonicalDatabaseError` by bare name, and in a full root run it failed at collection with
`cannot import name 'CanonicalDatabaseError' from 'conftest' (.../app/conftest.py)`. Run
on its own the same file is green, which is why it read as flaky rather than as a fact
about collection order. The guard it tests — `pytest -m db` refusing to run against the
canonical `fantabot` database — was never broken; it was its test that could not import.

`scripts/gate.sh` and `.github/workflows/ci.yml` both scoped their runs to `tests/` to get
around this, each with a comment saying `testpaths` would fix it. It does, and it is set
now. These two tests are what stops it being unset again by a merge that reads the
`markers` block and concludes the root run is meant to see `app/`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _paths import REPO, TESTS


def test_the_bare_conftest_is_this_suite_s_own_and_not_the_app_s() -> None:
    """The property directly. In a shadowed session this import returns `app/conftest.py`."""
    import conftest

    assert Path(conftest.__file__).resolve().parent == TESTS


def test_a_default_run_collects_the_tests_tree_and_nothing_from_app() -> None:
    """The cause, so a failure says *why* rather than only that the wrong file won.

    Collection only: nothing is executed, nothing is imported into this process, and no
    socket is opened.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout[-2000:]

    from_app = [line for line in result.stdout.splitlines() if line.startswith("app/")]
    assert not from_app, f"a root run collected {len(from_app)} tests from app/: {from_app[:3]}"
