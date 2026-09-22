"""numpy and scipy stay off the default lineup path, and randomness stays seeded.

The hourly `launchd` job runs from the live checkout, so the day numpy lands in
`pyproject.toml` every module it can reach becomes a way to break an armed job that has
nothing to do with the projection (SPEC A5, AD3). The projection branch may use both;
the path that submits today's lineup may not load either. Three guards, because each
catches a shape the others cannot:

* **1a, module scope** (AST). An `import numpy` at the top of `build.py` loads it for
  every run. `_importgraph.module_scope_*` reads only what an import *executes*, parent
  `__init__`s included.
* **1a, full graph with a declared cut** (AST). A lazy `import numpy` inside a function
  on the default branch is invisible at module scope and still runs every hour. So the
  whole design graph is walked, and the only edges allowed into anything that reaches
  numpy or scipy are the ones `EXPECTED_PROJECTION_EDGES` names — compared by exact
  equality, so a new lazy edge must be declared and a removed one deleted.
* **1b, runtime.** `importlib.import_module("numpy")` is invisible to any AST walk. A
  fresh interpreter blocks both at the import system and runs the default `lineup plan`
  and the job's own `lineup submit --arm --scheduled`, against fakes.

Guard 2 is SPEC's "randomness always goes through an injected, caller-seeded
`numpy.random.Generator`" made checkable: an unseeded draw in `domain/lineup` is a
backtest that cannot be replayed and a shadow report that cannot be recomputed.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import _importgraph as G
import pytest
from _paths import PACKAGE

HEAVY = ("numpy", "scipy")

#: The modules the default lineup — and the hourly job — runs through. Named, not derived:
#: this is the claim being made, and a derivation would move with the code it guards.
DEFAULT_PATH = (
    "fantabot.interface.lineup",
    "fantabot.application.lineup_submit",
    "fantabot.application.lineup_planner",
    "fantabot.domain.lineup.build",
    "fantabot.domain.lineup.bench",
    "fantabot.domain.lineup.schema",
    "fantabot.domain.lineup.positional",
)

#: `(importer, imported)` for every edge from the default path into the projection branch:
#: the lazy imports the projection is *allowed* to be reached through. Empty until the
#: branch exists; T18 added the first, `plan --model projection`'s own import. Exact
#: equality, like `test_layers.py`'s ratchets.
EXPECTED_PROJECTION_EDGES: frozenset[tuple[str, str]] = frozenset(
    {("fantabot.interface.lineup", "fantabot.application.lineup_projection")}
)


def _is_heavy(name: str) -> bool:
    return any(name == h or name.startswith(f"{h}.") for h in HEAVY)


def _reaches_heavy(name: str) -> bool:
    return _is_heavy(name) or (G.resolves(name) and any(G.reaches(name, h) for h in HEAVY))


def test_every_default_module_still_exists() -> None:
    """A rule about a renamed module is silently empty."""
    assert [m for m in DEFAULT_PATH if not G.resolves(m)] == []


@pytest.mark.parametrize("module", DEFAULT_PATH)
def test_importing_the_default_path_loads_neither(module: str) -> None:
    for heavy in HEAVY:
        assert not G.loads_at_import(module, heavy), (
            f"importing {module} loads {heavy}: "
            f"{' -> '.join(G.why(module, heavy, edges=G.module_scope_edges))}. "
            "Import it lazily, inside the projection branch."
        )


def test_the_edges_into_the_projection_branch_are_exactly_the_declared_ones() -> None:
    """Every edge from the default path's design graph into a module reaching numpy or
    scipy — stopping at the first such module, so an edge is the *entry* to the branch."""
    found: set[tuple[str, str]] = set()
    seen: set[str] = set()
    stack = list(DEFAULT_PATH)
    while stack:
        module = stack.pop()
        if module in seen or not G.resolves(module):
            continue
        seen.add(module)
        for name in G.direct_imports(module):
            if _reaches_heavy(name):
                found.add((module, name))
            else:
                stack.append(name)

    assert found == EXPECTED_PROJECTION_EDGES, (
        f"undeclared: {sorted(found - EXPECTED_PROJECTION_EDGES)}; "
        f"declared but gone: {sorted(EXPECTED_PROJECTION_EDGES - found)}"
    )


def test_cutting_the_declared_edges_leaves_neither_reachable() -> None:
    """The safety claim itself, stated without the frontier's arithmetic."""
    seen: set[str] = set()
    stack = list(DEFAULT_PATH)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        if G.resolves(module):
            stack.extend(
                name
                for name in G.direct_imports(module)
                if (module, name) not in EXPECTED_PROJECTION_EDGES
            )

    assert sorted(name for name in seen if _is_heavy(name)) == []


# -- guard 1b: the default path runs with both blocked ------------------------------------

_BLOCKED_RUN = textwrap.dedent(
    """
    import importlib.abc
    import socket
    import sys


    class _Blocked(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0] in {"numpy", "scipy"}:
                raise ImportError(f"{name} is blocked: the default lineup path loaded it")
            return None


    sys.meta_path.insert(0, _Blocked())
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass
    else:
        raise SystemExit("the blocker is inert: numpy imported anyway")


    def _no_socket(*args, **kwargs):
        raise AssertionError("a socket was opened")


    socket.socket.connect = _no_socket
    socket.socket.connect_ex = _no_socket
    socket.create_connection = _no_socket

    from cryptography.fernet import Fernet
    from typer.testing import CliRunner

    from fantabot import config
    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.interface.app import app


    class _Session:
        def commit(self): ...
        def rollback(self): ...
        def close(self): ...


    config.settings.fantabot_encryption_key = Fernet.generate_key().decode()
    config.settings.fantabot_league_id = 4103937
    config._DOTENV_INJECTED = {}
    database_manager._session_factory = _Session

    # 3 keepers + 27 broad-role outfielders: fields 3-4-3 and fills a 12-man bench.
    lineup_info = [
        {"pid": 1000 + i, "role": [6], "indexCompare": 5.0 - 0.1 * i, "plyr": f"GK{i}"}
        for i in range(3)
    ] + [
        {
            "pid": 2000 + i,
            "role": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16],
            "indexCompare": 8.0 - 0.1 * i,
            "plyr": f"Player{i}",
        }
        for i in range(27)
    ]
    apileague.my_team = lambda *a, **k: {"id": 10000003}
    apileague.competitions = lambda *a, **k: [{"id": 311681, "tmids": [10000003], "del": False}]
    apileague.teamLineup_read = lambda *a, **k: {
        "teamLineupDto": {"mday": 1, "cmday": 3, "tid": 0},
        "lineUpInfo": lineup_info,
    }
    apileague.lineup_settings = lambda *a, **k: {"mods": ["343"], "tbench": 12}
    apileague.roster_settings = lambda *a, **k: {"sroles": 2}
    apileague.league_status = lambda *a, **k: {"mstr": "2099-01-01T00:00:00", "mday": 3}
    posted = []
    apileague.teamLineup_submit = lambda _lid, body, **k: posted.append(body)

    runner = CliRunner()
    plan = runner.invoke(app, ["lineup", "plan", "--league", "4103937"])
    assert plan.exit_code == 0 and "XI:" in plan.output, (plan.output, repr(plan.exception))

    # The job's own argv (`schedule run` appends exactly these), armed, POSTing to a fake.
    submit = runner.invoke(
        app, ["lineup", "submit", "--league", "4103937", "--arm", "--scheduled"]
    )
    assert submit.exit_code == 0, (submit.output, repr(submit.exception))
    assert len(posted) == 1, (posted, submit.output)

    loaded = sorted(n for n in sys.modules if n.partition(".")[0] in {"numpy", "scipy"})
    assert loaded == [], loaded
    print("RAN WITHOUT NUMPY OR SCIPY")
    """
)


def test_the_default_plan_and_the_scheduled_submit_run_with_both_blocked(
    tmp_path: Path,
) -> None:
    """In a fresh interpreter, so nothing earlier in the session has loaded either.

    `HOME` and `USERPROFILE` point at `tmp_path` because a scheduled submit appends to
    `~/.fantabot/lineup_runs.jsonl`: two tests that forgot once wrote 34 fake runs into the
    operator's real record. The working directory is `tmp_path` too, so no `.env` is read.
    """
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "FANTABOT_AUTO_ACT": "true",
    }
    result = subprocess.run(
        [sys.executable, "-c", _BLOCKED_RUN],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-3000:]
    assert "RAN WITHOUT NUMPY OR SCIPY" in result.stdout
    runs = (tmp_path / ".fantabot" / "lineup_runs.jsonl").read_text().splitlines()
    assert len(runs) == 1, "the scheduled run left no record in the redirected home"


# -- guard 2: no unseeded randomness in domain/lineup -------------------------------------

#: Build a generator. Legal only with a seed that is not `None`.
_CONSTRUCTORS = frozenset({"default_rng", "RandomState", "SeedSequence", "Random"})
#: `numpy.random` names that are types or bit generators, not draws from global state.
_NUMPY_RANDOM_TYPES = frozenset(
    {"Generator", "BitGenerator", "PCG64", "PCG64DXSM", "Philox", "SFC64", "MT19937"}
)
#: A method drawing from scipy's global state unless this keyword is passed.
_SEEDED_BY = {"rvs": "random_state", "resample": "seed"}


def _unseeded(source: str) -> list[str]:
    """Every unseeded draw in `source`, as `line: reason`."""
    found: list[str] = []
    allowed = _CONSTRUCTORS | _NUMPY_RANDOM_TYPES
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import) and any(a.name == "random" for a in node.names):
            found.append(f"{line}: stdlib `random` is process-global state")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "random":
                found.append(f"{line}: stdlib `random` is process-global state")
            elif node.module == "numpy" and any(a.name == "random" for a in node.names):
                found.append(f"{line}: `from numpy import random` hides global draws")
            elif node.module == "numpy.random":
                found.extend(
                    f"{line}: numpy's global RNG, `{a.name}`"
                    for a in node.names
                    if a.name not in allowed
                )
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _CONSTRUCTORS and _no_seed(node):
                found.append(f"{line}: `{name}()` with no seed")
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "random"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id in {"np", "numpy"}
                and name not in allowed
            ):
                found.append(f"{line}: numpy's global RNG, `np.random.{name}`")
            elif name in _SEEDED_BY and all(k.arg != _SEEDED_BY[name] for k in node.keywords):
                found.append(f"{line}: `.{name}()` without `{_SEEDED_BY[name]}=`")
    return found


def _no_seed(call: ast.Call) -> bool:
    seeds = [*call.args[:1], *(k.value for k in call.keywords if k.arg == "seed")]
    return not seeds or all(isinstance(s, ast.Constant) and s.value is None for s in seeds)


@pytest.mark.parametrize(
    "source",
    [
        "import random\nx = random.random()",
        "from random import shuffle",
        "import numpy as np\nrng = np.random.default_rng()",
        "import numpy as np\nrng = np.random.default_rng(None)",
        "import numpy as np\nrng = np.random.default_rng(seed=None)",
        "from numpy.random import default_rng\nrng = default_rng()",
        "import numpy as np\nx = np.random.normal(0.0, 1.0)",
        "import numpy as np\nnp.random.seed(7)",
        "from numpy.random import normal",
        "from numpy import random",
        "from scipy import stats\nx = stats.norm.rvs(size=3)",
        "x = kde.resample(100)",
    ],
)
def test_the_detector_flags_an_unseeded_draw(source: str) -> None:
    """Checked on snippets, because `domain/lineup` draws nothing yet: a scan that finds
    nothing would pass just the same with a detector that cannot see anything."""
    assert _unseeded(source), source


@pytest.mark.parametrize(
    "source",
    [
        "import numpy as np\nrng = np.random.default_rng(seed)",
        "import numpy as np\nrng = np.random.default_rng(seed=key)",
        "import numpy as np\ndef draw(rng: np.random.Generator) -> float:\n    return rng.normal()",
        "from numpy.random import Generator, SeedSequence",
        "from scipy import stats\nx = stats.norm.rvs(size=3, random_state=rng)",
        "x = kde.resample(100, seed=rng)",
    ],
)
def test_the_detector_passes_a_seeded_or_injected_draw(source: str) -> None:
    assert _unseeded(source) == [], source


def test_domain_lineup_draws_nothing_unseeded() -> None:
    lineup = PACKAGE / "domain" / "lineup"
    files = sorted(lineup.rglob("*.py"))
    assert lineup / "build.py" in files, "the scan is looking somewhere else"

    offenders = [
        f"{path.relative_to(PACKAGE)}:{hit}"
        for path in files
        for hit in _unseeded(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "randomness in domain/lineup goes through an injected numpy Generator, seeded by "
        f"the caller: {offenders}"
    )
