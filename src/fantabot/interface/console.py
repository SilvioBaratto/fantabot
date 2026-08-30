"""The one `rich.Console` in the package.

There were seven: `cli.py`, `aste/cli.py`, `asta_engine/cli.py`, `login.py`,
`fantalab_login.py`, and one each in the two Classic modules deleted in P3.

**Why a module of its own rather than importing `cli.console`.** `cli.py` imports
the two sub-CLIs at module scope in order to register their commands, so an
interface module importing `console` back from `cli` is a cycle — reproduced, it
raises `ImportError` at the partially-initialised module. A leaf module both sides
can import is the whole fix.

**Why one at all.** A `Console` caches width, colour and file in `__init__`, so
seven of them are seven independent readings of the environment, taken at whatever
moment each module first gets imported. `tests/conftest.py` already sets `NO_COLOR`,
`TERM` and `COLUMNS` at module scope specifically because of that, and the golden
harness asserts bytes that those settings decide. One console is one reading.
"""

from rich.console import Console

#: Bare, deliberately: every setting comes from the environment, which is what
#: `conftest.py` and the golden harness control.
console = Console()
