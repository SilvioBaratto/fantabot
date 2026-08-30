"""The shipped artefacts are inside the package, and are found from anywhere.

`mantra_compat.json` is the matcher's input -- a wrong cell builds lineups the platform
rejects -- so where it is read from is not a packaging detail. Two things have to hold and
neither is obvious from reading `resources.py`: the files must sit inside the importable
package rather than beside it, or a non-editable install ships without them; and the
lookup must not depend on the working directory, because `asta legality` is run from
wherever the operator happens to be.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fantabot.resources import COMPAT_FILENAME, SCHEMI_FILENAME, data_dir


def test_both_artefacts_live_inside_the_package() -> None:
    """Beside the code, not beside the repository. A wheel carries one and not the other."""
    import fantabot

    package = Path(fantabot.__file__).resolve().parent
    for name in (SCHEMI_FILENAME, COMPAT_FILENAME):
        path = (data_dir() / name).resolve()
        assert path.is_file(), f"{name} is not where resources.data_dir() says it is"
        assert package in path.parents, f"{name} is outside the package: {path}"


def test_the_matrix_parses_and_is_the_whole_table() -> None:
    """The one-entry matrix passed for a week; a file that loads is not a file that is right."""
    matrix = json.loads((data_dir() / COMPAT_FILENAME).read_text(encoding="utf-8"))

    assert len(matrix["formazioni"]) == 11
    assert all(len(f["slots"]) == 11 for f in matrix["formazioni"])


def test_the_lookup_does_not_depend_on_the_working_directory() -> None:
    """Run in a fresh interpreter from `/`, which is the strongest form of the claim.

    Asserting in-process would prove nothing: the path is computed from the import
    system, so a CWD-dependent bug would still resolve correctly under pytest, which
    starts in the repository root.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "from fantabot.domain.asta.legality import load_compat;"
         " print(len(load_compat().formazioni))"],
        cwd="/", capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "11"
