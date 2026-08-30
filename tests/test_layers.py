"""Which layer each module belongs to, and what that layer is allowed to reach.

**Why a ratchet and not a clean rule.** The tree does not satisfy these rules today —
six modules that read as decision logic reach Postgres or the agent SDK from inside a
function body. A rule that fails immediately gets an `xfail` and stops meaning anything.
So the current violations are written down, compared for **exact equality**, and removed
by the splits that fix them. That direction matters in both senses: a new violation
fails, and so does a fixed one that nobody recorded, which is what keeps the list from
rotting into a permanent allowlist.

**Why the table carries names that do not exist yet.** W6 moves this tree into
`domain/`, `application/`, `adapters/` and `interface/`. Carrying both the old and the
new prefixes through the move means a package rename is a rename, not a rewrite of this
file — and the rules keep applying while the tree is half-moved, which is exactly when
a layer is easiest to break.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _importgraph as G

# --------------------------------------------------------------------------------------
# The table. Longest prefix wins, so a package can be placed once and a single module
# inside it overridden. Both the current names and their W6 destinations are listed.
# --------------------------------------------------------------------------------------

LAYERS: dict[str, str] = {
    # -- domain: decisions. Pure by intent; the rules below are what make that true.
    "fantabot.asta_engine": "domain",
    "fantabot.aste": "domain",
    "fantabot.news": "domain",
    "fantabot.mantra_grid": "domain",
    "fantabot.club_names": "domain",
    "fantabot.parsing": "domain",
    "fantabot.data_sources.models": "domain",
    "fantabot.tokens.claims": "domain",
    "fantabot.tokens.errors": "domain",
    "fantabot.tokens.capture": "domain",
    "fantabot.tokens.fantalab": "domain",
    "fantabot.tokens.status": "domain",
    "fantabot.domain": "domain",
    # -- application: orchestration. May use adapters; may not be a user interface.
    "fantabot.asta_engine.plan": "application",
    "fantabot.aste.loader": "application",
    "fantabot.aste.supervisor": "application",
    "fantabot.news.pipeline": "application",
    "fantabot.mantra_grid.collect": "application",
    "fantabot.mantra_grid.writer": "application",
    "fantabot.pricing": "application",
    "fantabot.login": "application",
    "fantabot.fantalab_login": "application",
    "fantabot.application": "application",
    # -- adapters: everything that talks to the world.
    "fantabot.db": "adapters",
    "fantabot.agentkit": "adapters",
    "fantabot.fantalab": "adapters",
    "fantabot.scrapers": "adapters",
    "fantabot.apileague": "adapters",
    "fantabot.browser": "adapters",
    "fantabot.config": "adapters",
    # `state.storage_state_path` resolves a path out of Settings. Its docstring calls
    # it "one function" and it imports nothing from `db/` — but reading configuration
    # to name a file on disk is infrastructure, and placing it in the domain layer
    # would have made `.env` a dependency of every pure test that touched it.
    "fantabot.state": "adapters",
    "fantabot.tokens": "adapters",
    "fantabot.aste.stream": "adapters",
    "fantabot.aste.transport": "adapters",
    "fantabot.aste.landing": "adapters",
    "fantabot.aste.client": "adapters",
    "fantabot.news.store": "adapters",
    "fantabot.data_sources.news_sentiment": "adapters",
    "fantabot.adapters": "adapters",
    # -- interface: the CLI, and only the CLI.
    "fantabot.cli": "interface",
    "fantabot.interface": "interface",
    "fantabot.asta_engine.cli": "interface",
    "fantabot.aste.cli": "interface",
}

#: Namespace packages carry no code and belong to no layer.
UNPLACED = {"fantabot", "fantabot.asta_engine", "fantabot.aste", "fantabot.news",
            "fantabot.tokens", "fantabot.db", "fantabot.db.models",
            "fantabot.db.repositories", "fantabot.agentkit", "fantabot.fantalab",
            "fantabot.scrapers", "fantabot.mantra_grid", "fantabot.data_sources",
            "fantabot.interface"}


def layer_of(module: str) -> str:
    """Longest matching prefix. `fantabot.aste.cli` is interface, not domain."""
    best = ""
    for prefix in LAYERS:
        if (module == prefix or module.startswith(f"{prefix}.")) and len(prefix) > len(best):
            best = prefix
    return LAYERS[best] if best else "unplaced"


# --------------------------------------------------------------------------------------
# The rules. Each names a failure it has actually prevented or would have.
# --------------------------------------------------------------------------------------

#: A domain module that reaches any of these is not a decision, it is a shell. Includes
#: `fantabot.interface` and `fantabot.config`: a pure module that reads settings has
#: tests that depend on `.env`, and one that prints has tests that depend on a terminal.
FORBIDDEN_TO_DOMAIN = (
    "fantabot.db", "fantabot.interface", "fantabot.browser", "fantabot.config",
    "sqlalchemy", "psycopg2", "playwright", "httpx", "claude_agent_sdk", "typer", "rich",
)

#: Typer is the CLI framework. A non-interface module reaching it means a command was
#: defined outside the command layer, which is how `python cli.py` and `fantabot` came
#: to show different menus.
CLI_ONLY = ("typer",)


def _violations(rule: object) -> set[tuple[str, str]]:
    """`(module, target)` for every module that breaks `rule`. Sorted set, for equality."""
    found: set[tuple[str, str]] = set()
    for module in G.modules():
        if module in UNPLACED:
            continue
        layer = layer_of(module)
        targets = FORBIDDEN_TO_DOMAIN if rule == "domain" else CLI_ONLY
        if rule == "domain" and layer != "domain":
            continue
        if rule == "cli" and layer == "interface":
            continue
        found.update((module, t) for t in targets if G.reaches(module, t))
    return found


#: Measured on 2026-08-30, and every entry has a task that removes it. Exact equality,
#: not a subset: a violation someone fixed without deleting its line here would leave
#: the list looking like it still protects something.
EXPECTED_DOMAIN_VIOLATIONS: set[tuple[str, str]] = {
    # P11-4 — `pool.py:87`, and the two modules that import it for its pure half.
    ("fantabot.news.pool", "fantabot.db"),
    ("fantabot.news.pool", "sqlalchemy"),
    ("fantabot.news.prompt", "fantabot.db"),
    ("fantabot.news.prompt", "sqlalchemy"),
    # P11-5 — the agent call that parses a typed line lives in the decision layer.
    # `config` rides the same import: `agentkit.options` builds its options from
    # Settings, so both edges are cut by the one split.
    ("fantabot.asta_engine.stateentry", "claude_agent_sdk"),
    ("fantabot.asta_engine.stateentry", "fantabot.config"),
}

EXPECTED_CLI_VIOLATIONS: set[tuple[str, str]] = set()


def _report(actual: set[tuple[str, str]], expected: set[tuple[str, str]]) -> str:
    new = sorted(actual - expected)
    gone = sorted(expected - actual)
    lines = []
    for module, target in new:
        path = " -> ".join(G.why(module, target)) or f"{module} -> {target}"
        lines.append(f"  NEW      {module} reaches {target}\n           via {path}")
    for module, target in gone:
        lines.append(
            f"  FIXED    {module} no longer reaches {target}"
            f" — delete its line from the expected set in the same commit"
        )
    return "\n".join(lines)


def test_the_domain_layer_stays_out_of_the_world() -> None:
    """Pure modules are why this repository is testable; the leaks are named and shrinking."""
    actual = _violations("domain")
    assert actual == EXPECTED_DOMAIN_VIOLATIONS, (
        "the domain layer's dependency list moved:\n"
        + _report(actual, EXPECTED_DOMAIN_VIOLATIONS)
    )


def test_only_the_interface_layer_knows_about_typer() -> None:
    actual = _violations("cli")
    assert actual == EXPECTED_CLI_VIOLATIONS, (
        "a command framework escaped the command layer:\n"
        + _report(actual, EXPECTED_CLI_VIOLATIONS)
    )


class TestTheTableItself:
    """A layer table is silently wrong in two ways, and both have to be closed.

    `reaches` on a module that does not exist returns False — so a typo in `LAYERS`
    exempts a module rather than failing. And a module absent from the table is
    unplaced, which is the same exemption arrived at by forgetting.
    """

    def test_every_name_in_the_table_resolves_to_something(self) -> None:
        known = set(G.modules())
        missing = sorted(
            prefix
            for prefix in LAYERS
            if prefix not in known and not any(m.startswith(f"{prefix}.") for m in known)
        )
        # The W6 destinations do not exist yet, by design.
        pending = {"fantabot.domain", "fantabot.application", "fantabot.adapters"}
        assert set(missing) <= pending, f"LAYERS names modules that do not exist: {missing}"

    def test_every_module_is_placed(self) -> None:
        orphans = sorted(m for m in G.modules() if m not in UNPLACED and layer_of(m) == "unplaced")
        assert not orphans, (
            "these modules are in no layer, so no rule applies to them — place each in "
            f"LAYERS: {orphans}"
        )

    def test_longest_prefix_wins(self) -> None:
        """`fantabot.aste` is domain and `fantabot.aste.cli` is interface."""
        assert layer_of("fantabot.aste.reducer") == "domain"
        assert layer_of("fantabot.aste.cli") == "interface"
        assert layer_of("fantabot.aste.stream") == "adapters"
