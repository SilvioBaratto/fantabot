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

import _importgraph as G

# --------------------------------------------------------------------------------------
# The table. Longest prefix wins, so a package can be placed once and a single module
# inside it overridden. Both the current names and their W6 destinations are listed.
# --------------------------------------------------------------------------------------

LAYERS: dict[str, str] = {
    # -- domain: decisions. Pure by intent; the rules below are what make that true.
    "fantabot.domain.asta": "domain",
    "fantabot.domain.harvest": "domain",
    "fantabot.domain.news": "domain",
    "fantabot.domain.mantra": "domain",
    "fantabot.domain.shared.club_names": "domain",
    "fantabot.domain.shared.parsing": "domain",
    # Where the packaged JSON artefacts are. A constant location, not a setting, so
    # a pure module may ask it without acquiring a dependency on the environment.
    "fantabot.domain.shared.resources": "domain",
    "fantabot.domain.shared.values": "domain",
    "fantabot.domain.tokens.claims": "domain",
    "fantabot.domain.tokens.errors": "domain",
    "fantabot.domain.tokens.capture": "domain",
    "fantabot.domain.tokens.fantalab": "domain",
    # Fernet encrypt/decrypt over a key passed in as an argument. `tokens/__init__.py`
    # lists it among the three pure modules; only `store` and `fantalab_store` do I/O.
    "fantabot.domain.tokens.crypto": "domain",
    "fantabot.domain.tokens.status": "domain",
    "fantabot.domain": "domain",
    # -- application: orchestration. May use adapters; may not be a user interface.
    "fantabot.application.asta_planner": "application",
    "fantabot.application.harvest_loader": "application",
    "fantabot.application.harvest_supervisor": "application",
    "fantabot.application.news_fetcher": "application",
    "fantabot.application.mantra_collector": "application",
    "fantabot.application.pricing": "application",
    "fantabot.application.auth_login": "application",
    "fantabot.application.fantalab_login": "application",
    "fantabot.application": "application",
    # -- adapters: everything that talks to the world.
    "fantabot.adapters.persistence": "adapters",
    "fantabot.adapters.agent": "adapters",
    "fantabot.adapters.http.fantalab": "adapters",
    "fantabot.adapters.scraping": "adapters",
    "fantabot.adapters.http.apileague": "adapters",
    "fantabot.adapters.browser.capture": "adapters",
    "fantabot.config": "adapters",
    # `state.storage_state_path` resolves a path out of Settings. Its docstring calls
    # it "one function" and it imports nothing from `db/` — but reading configuration
    # to name a file on disk is infrastructure, and placing it in the domain layer
    # would have made `.env` a dependency of every pure test that touched it.
    "fantabot.adapters.browser.storage_state": "adapters",
    "fantabot.domain.tokens": "domain",
    "fantabot.adapters.http.harvest.stream": "adapters",
    "fantabot.adapters.http.harvest.transport": "adapters",
    "fantabot.adapters.files.landing": "adapters",
    # "The only module here that touches disk", says its own docstring. It was filed
    # under application until the W6 destination map contradicted it.
    "fantabot.adapters.files.mantra_writer": "adapters",
    "fantabot.adapters.http.harvest.client": "adapters",
    # `store.py` holds only `build_row`, which is pure — it reached the database
    # solely by importing `PoolPlayer` from a module that did.
    "fantabot.adapters.persistence.news_pool": "adapters",
    "fantabot.adapters.persistence.news_sentiment": "adapters",
    "fantabot.adapters": "adapters",
    # -- interface: the CLI, and only the CLI.
    "fantabot.interface.app": "interface",
    "fantabot.interface": "interface",
    "fantabot.interface.asta": "interface",
    "fantabot.interface.harvest": "interface",
}

#: Packages that carry no code and belong to no layer. The four layer roots are here
#: too: they are directories the move creates, holding a one-line stub, and a rule about
#: a directory would be a rule about nothing.
UNPLACED = {"fantabot.domain", "fantabot.application", "fantabot.adapters",
            "fantabot.interface",
            "fantabot", "fantabot.domain.asta", "fantabot.domain.harvest", "fantabot.domain.news",
            "fantabot.domain.tokens", "fantabot.adapters.tokens", "fantabot.adapters.persistence", "fantabot.adapters.persistence.models",
            "fantabot.adapters.persistence.repositories", "fantabot.adapters.agent", "fantabot.adapters.http.fantalab",
            "fantabot.adapters.scraping", "fantabot.domain.mantra", "fantabot.data_sources"}


def layer_of(module: str) -> str:
    """Longest matching prefix. `fantabot.interface.harvest` is interface, not domain."""
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
    "fantabot.adapters.persistence", "fantabot.interface", "fantabot.adapters.browser.capture", "fantabot.config",
    "sqlalchemy", "psycopg2", "playwright", "httpx", "claude_agent_sdk", "typer", "rich",
)

#: Typer is the CLI framework. A non-interface module reaching it means a command was
#: defined outside the command layer, which is how `python cli.py` and `fantabot` came
#: to show different menus.
CLI_ONLY = ("typer",)

#: The application layer orchestrates; it does not present. Reaching the interface layer
#: points the dependency the wrong way, and reaching Rich means it is deciding how
#: something looks rather than what it is. Three modules did the first until P12-11 gave
#: them an injected `Reporter`; one still does the second.
FORBIDDEN_TO_APPLICATION = ("typer", "rich", "fantabot.interface", "playwright")


def _violations(rule: object) -> set[tuple[str, str]]:
    """`(module, target)` for every module that breaks `rule`. Sorted set, for equality."""
    found: set[tuple[str, str]] = set()
    for module in G.modules():
        if module in UNPLACED:
            continue
        layer = layer_of(module)
        targets = {
            "domain": FORBIDDEN_TO_DOMAIN,
            "application": FORBIDDEN_TO_APPLICATION,
            "cli": CLI_ONLY,
        }[str(rule)]
        if rule in ("domain", "application") and layer != rule:
            continue
        if rule == "cli" and layer == "interface":
            continue
        found.update((module, t) for t in targets if G.reaches(module, t))
    return found


#: Empty as of P11-5, and it started at seven. Kept rather than replaced by a bare
#: `assert not actual`, because the next module to break a layer will need somewhere to
#: be recorded while its fix is written, and the exact-equality comparison is what stops
#: that record from outliving the fix.
EXPECTED_DOMAIN_VIOLATIONS: set[tuple[str, str]] = set()

EXPECTED_CLI_VIOLATIONS: set[tuple[str, str]] = set()

EXPECTED_APPLICATION_VIOLATIONS: set[tuple[str, str]] = set()


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


def test_the_application_layer_orchestrates_rather_than_presents() -> None:
    """It may use adapters; it may not be a user interface, or know about one."""
    actual = _violations("application")
    assert actual == EXPECTED_APPLICATION_VIOLATIONS, (
        "the application layer's dependency list moved:\n"
        + _report(actual, EXPECTED_APPLICATION_VIOLATIONS)
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
        """`fantabot.domain.harvest` is domain and `fantabot.interface.harvest` is interface."""
        assert layer_of("fantabot.domain.harvest.reducer") == "domain"
        assert layer_of("fantabot.interface.harvest") == "interface"
        assert layer_of("fantabot.adapters.http.harvest.stream") == "adapters"
