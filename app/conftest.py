"""Suite-wide setup for both of the app's testpaths.

This sits at the rootdir rather than under either `tests/` or
`fantabot_app/api/tests/` because `pyproject.toml` declares two testpaths and the console
settings below have to be in place before *either* imports the CLI.

**The console is pinned, and not for tidiness.** `app-ci` was red on all three OSes on a
single assertion — `assert "--from" in result.output` in `test_harvest_home.py` — and the
recorded diagnosis, "a Rich console-width artefact", was wrong. Measured against the
actual CI log: the output carries ANSI, and Rich's option highlighter styles the leading
dash *separately* from the rest of the name, so `--from` reaches the test as
`\x1b[1;36m-\x1b[0m\x1b[1;36m-from\x1b[0m` and no substring search can find it. Colour, not
width. GitHub Actions is what turns it on; nothing local does, which is why the suite was
green on the machine that wrote it and red on every runner.

Width is pinned for the sibling reason and it is real too: Rich lays `--help` out in a
table sized to `COLUMNS`, and a narrow window hyphenates an option name out of existence.
Cheaper to fix both here than to discover the second one on a runner as well.

**Module scope, not a fixture.** A Rich `Console` reads these variables in `__init__` and
caches the answer, and `fantabot_app/cli.py` builds one at import. By the time a fixture
body runs the decision is already made. conftest is imported before the test modules that
import the CLI, so here it is still early enough. This mirrors `tests/conftest.py` in the
`fantabot` tree, which solved the same problem for the same reason.
"""

from __future__ import annotations

import os

for _forced in ("FORCE_COLOR", "CLICOLOR_FORCE", "TTY_COMPATIBLE", "CLICOLOR"):
    os.environ.pop(_forced, None)
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
os.environ["COLUMNS"] = "200"
