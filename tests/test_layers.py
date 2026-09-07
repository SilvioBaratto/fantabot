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

from collections.abc import Callable, Iterable
from pathlib import Path

import _importgraph as G

# --------------------------------------------------------------------------------------
# The table. Longest prefix wins, so a package can be placed once and a single module
# inside it overridden. Both the current names and their W6 destinations are listed.
# --------------------------------------------------------------------------------------

LAYERS: dict[str, str] = {
    # -- domain: decisions. Pure by intent; the rules below are what make that true.
    "fantabot.domain.asta": "domain",
    "fantabot.domain.classic": "domain",
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
    # The cooperative stop flag. An adapter for the same reason `lock.py` is one: the
    # decision to stop is the caller's, and this only writes it down where another
    # process can see it.
    "fantabot.adapters.files.stopflag": "adapters",
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
    # `python -m fantabot`, which is how the app's supervisor spawns the CLI: its own
    # virtualenv has no `fantabot` on PATH. Three lines that call `interface.app`, so it
    # is the command layer by every rule below — including the one about typer.
    "fantabot.__main__": "interface",
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

#: The writing names, and the adapter each lives in. `interface/` holds no decision the
#: app also needs, and the sharpest form of that rule is the acting one: a command body
#: that submits a lineup or raises a bid is a decision the app can only reach by
#: reimplementing it — which is how `endpoints/asta.py` came to build a `PlanRequest`
#: that differed from `asta optimize`'s in ten inputs.
#:
#: **Reads stay legal.** The printers need `my_team`, `read_snapshot` and the rest; a
#: rule against the whole adapter would forbid `lineup show` from showing anything.
WRITING_NAMES: dict[str, str] = {
    "teamLineup_submit": "fantabot.adapters.http.apileague",
    "place_raise": "fantabot.adapters.http.fantalab.rtdb",
}


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

#: The T-spine ratchet. Two entries, both scheduled for deletion: `lineup.py:247`
#: (`teamLineup_submit`) by 3.3, and `asta.py:576`/`:1000` (`place_raise`, both handed to
#: a `room.LotRouter`) by 3.9b. Recorded as `(module, name)` rather than per line so a
#: reformat is not a false failure — the question is whether the command layer can act,
#: not how many times it says so.
EXPECTED_WRITING_VIOLATIONS: set[tuple[str, str]] = {
    ("fantabot.interface.asta", "place_raise"),
    ("fantabot.interface.lineup", "teamLineup_submit"),
}



def writing_violations(
    modules: Iterable[str],
    *,
    names_used: Callable[[str], frozenset[str]],
    reaches: Callable[[str, str], bool],
    layer: Callable[[str], str],
) -> set[tuple[str, str]]:
    """`(module, name)` for every interface module that names a writing call.

    The lookups are arguments rather than `G.` calls so the two failures this rule has
    to catch — a new writing call, and a ratchet entry outliving its fix — can be tested
    against a synthetic tree. Testing them against the real one would mean editing
    `src/` to prove a test works, and the edit is what the test is for.

    Both conditions are required. A name alone is not enough (`place_raise` appears in
    `interface/asta.py`'s prose about arming), and reaching the adapter alone is not
    either (every printer imports it).
    """
    found: set[tuple[str, str]] = set()
    for module in modules:
        if module in UNPLACED or layer(module) != "interface":
            continue
        used = names_used(module)
        found.update(
            (module, name)
            for name, owner in WRITING_NAMES.items()
            if name in used and reaches(module, owner)
        )
    return found


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


def test_the_command_layer_holds_no_decision_the_app_also_needs() -> None:
    """`interface/` may parse options, print, and choose an exit code. It may not act."""
    actual = writing_violations(
        G.modules(), names_used=G.names_used, reaches=G.reaches, layer=layer_of
    )
    assert actual == EXPECTED_WRITING_VIOLATIONS, (
        "the command layer's acting calls moved:\n"
        + _report(actual, EXPECTED_WRITING_VIOLATIONS)
    )


class TestTheWritingRuleItself:
    """The two ways this rule goes quiet, exercised against a tree that is not `src/`.

    Neither is hypothetical. A rule keyed on a function name is empty the day that
    function is renamed, and a ratchet whose entries outlive their fixes is an
    allowlist — which is the failure the domain ratchet's exact-equality comparison was
    introduced to prevent, one layer down.
    """

    @staticmethod
    def _fake(used: dict[str, set[str]], layers: dict[str, str]) -> dict[str, object]:
        return {
            "names_used": lambda m: frozenset(used.get(m, ())),
            "reaches": lambda _m, _t: True,
            "layer": lambda m: layers.get(m, "unplaced"),
        }

    def test_a_new_writing_call_from_the_command_layer_is_a_violation(self) -> None:
        fake = self._fake(
            {"fantabot.interface.newcmd": {"place_raise"}},
            {"fantabot.interface.newcmd": "interface"},
        )
        assert writing_violations(["fantabot.interface.newcmd"], **fake) == {  # type: ignore[arg-type]
            ("fantabot.interface.newcmd", "place_raise")
        }

    def test_the_same_call_from_application_is_not(self) -> None:
        """That is the destination, not a leak: 3.3 and 3.9b move these calls there."""
        fake = self._fake(
            {"fantabot.application.asta_session": {"place_raise"}},
            {"fantabot.application.asta_session": "application"},
        )
        assert writing_violations(["fantabot.application.asta_session"], **fake) == set()  # type: ignore[arg-type]

    def test_naming_a_writing_call_in_prose_is_not_a_violation(self) -> None:
        """`interface/asta.py` explains arming by naming `place_raise` twice."""
        module = "fantabot.interface.asta"
        assert "place_raise" in Path(G.SRC / "fantabot" / "interface" / "asta.py").read_text(
            encoding="utf-8"
        )
        prose_only = self._fake({module: set()}, {module: "interface"})
        assert writing_violations([module], **prose_only) == set()  # type: ignore[arg-type]

    def test_every_writing_name_still_exists_in_the_adapter_it_names(self) -> None:
        """A renamed function empties this rule silently; this is what makes it fail."""
        for name, owner in WRITING_NAMES.items():
            assert name in G.defines(owner), (
                f"{owner} no longer defines {name!r} — the writing rule is scanning for a "
                "name that does not exist, so it currently forbids nothing"
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
