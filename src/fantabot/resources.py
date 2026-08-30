"""Where the data files that ship with the package live.

Two artefacts are code in every sense that matters: `mantra_compat.json` is the 11 x 11 x
12 legality matrix `asta_engine.legality` matches against, and `mantra_schemi.json` is the
schema list it is aligned to. A wrong cell builds lineups the platform rejects.

They used to sit in the repository's `data/` directory and were reached two different
ways: `legality.py` climbed four levels out of its own module path, and `mantra-grid
--write` wrote to `settings.fantabot_data_dir`, which defaults to `./data` and is
therefore relative to wherever the process was started. Those two agree only when the
CWD is the repository root -- so `fantabot asta legality` from anywhere else read a
matrix the writer would not have written, and a `pip install` that was not editable had
no `data/` at all.

`importlib.resources` fixes both, and one more that had not happened yet: `parents[3]` is
a hard-coded depth, and W6 moves `legality.py` one level deeper, where it would have
resolved to `src/` and raised. That climb was the last of its kind in `src/`.

The rest of `data/` -- the CSV seed, the scraped landing zones, `storage_state.json` -- is
runtime state, not package data, and stays under `settings.fantabot_data_dir`.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

#: The 11 Mantra schemi, each with its 11 named slots.
SCHEMI_FILENAME = "mantra_schemi.json"
#: The out-of-position matrix: 11 schemi x 11 slots x 12 roles.
COMPAT_FILENAME = "mantra_compat.json"


def data_dir() -> Path:
    """The packaged `data/` directory.

    `Path` rather than `Traversable` because the caller writes here too, and because
    every consumer already speaks `Path`. Sound for this package, which is installed
    from a directory rather than a zip.
    """
    return Path(str(files("fantabot") / "data"))
