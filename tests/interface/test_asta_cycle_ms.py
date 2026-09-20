"""Every journal row carries `cycle_ms`, including the two that mean "the loop is in trouble".

`waiting_row` and `error_row` were written **unwrapped** — `journal.write(...)` directly,
bypassing `_timed_journal` — so the rows that record a skipped poll and a crashed one were
the only rows with no timing at all. The recorded evening's stall analysis was done on gaps
between rows precisely because the rows themselves did not say.

**The clock starts at the top of the poll, not at `cycle`.** It used to start inside
`target_of`, which is only reached when a lot is on the block, so there was no clock running
for a waiting poll to read. One field, one meaning: the whole poll, read included. Nothing
is lost by the redefinition — `cycle_ms` postdates the 2026-09-01 evening and is null across
every recorded row.

Read from the source rather than driven through a live room: the loops are Typer bodies with
a Rich screen and a network client in them, and what is being asserted is a wiring property
that an AST can see and a fake room cannot make more true.

**One module, since 3.9a — and that is the third shape this file has had.** The room's loop
moved into `application/asta_session.py` at 3.6b, leaving `asta bid`'s pair inline; 3.9a
lifted the bidder onto the same composition, so both rows are now built in exactly one place
and neither Typer body builds any. The scan is written to say that rather than to count two
per surface: a file asserting `asta bid` still holds its own pair would go red on the lift
that removed the duplication, which is backwards.

The clock itself did **not** move, on either lift: `cycle_ms` is still measured in
`interface/`, around the journal each surface hands its session, for the same reason
`interface/asta.py::_today` is the asta feature's only calendar read. That is why the two
halves below are separate claims — the rows reach *a* sink in `application/`, and each
surface injects the *timed* one. Either half alone passes over the defect.
"""

from __future__ import annotations

import ast

import pytest
from _paths import module_file

ASTA = "fantabot.interface.asta"
SESSION = "fantabot.application.asta_session"


def _tree(module: str = ASTA) -> ast.Module:
    return ast.parse(module_file(module).read_text(encoding="utf-8"))


def _calls_named(tree: ast.AST, *names: str) -> list[ast.Call]:
    wanted = set(names)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in wanted)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in wanted)
        )
    ]


def _row_builder_calls(tree: ast.AST) -> list[ast.Call]:
    return _calls_named(tree, "waiting_row", "error_row")


def test_the_row_builders_are_called_at_all() -> None:
    """A scan over nothing reports success; this is what makes the rest mean something.

    Two rows, once, in the lifted loop that produces them — and **none** in the Typer bodies,
    which is the property 3.9a bought. A third copy appearing in `interface/` is a command
    that has started journaling its own trouble rows again, with its own idea of when.
    """
    assert len(_row_builder_calls(_tree(SESSION))) == 2, "the lifted loop's two rows"
    assert _row_builder_calls(_tree()) == [], (
        "a Typer body builds a trouble row of its own: the loop is not the only place a "
        "skipped or crashed poll is recorded, so the two surfaces can disagree about when"
    )


@pytest.mark.parametrize("builder", ["waiting_row", "error_row"])
def test_every_row_goes_through_the_timed_journal(builder: str, module: str = SESSION) -> None:
    """`journal.write(waiting_row(...))` is the defect. The injected sink is the fix.

    In `AstaSession.run` that sink is whatever the caller composed the session with — the
    timed one on both surfaces, asserted separately below.
    """
    tree = _tree(module)
    untimed = [
        call.lineno
        for call in _calls_named(tree, "write")
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "write"
        and any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == builder
            for arg in call.args
        )
    ]

    assert not untimed, (
        f"{builder} is journaled unwrapped at {untimed} — those rows carry no cycle_ms, "
        "and they are exactly the rows that mean the loop is in trouble"
    )


@pytest.mark.parametrize("builder", ["waiting_row", "error_row"])
def test_and_reaches_the_timed_one(
    builder: str, module: str = SESSION, sink: str = "_journal"
) -> None:
    """The lifted loop journals through the sink it was composed with, not through one it
    reaches for — which is what lets `cycle_ms` be measured a layer out."""
    tree = _tree(module)
    timed = [
        call
        for call in _calls_named(tree, sink)
        if any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == builder
            for arg in call.args
        )
    ]

    assert len(timed) == 1, f"{builder} reaches {sink} {len(timed)} times in {module}, expected 1"


#: The two doors into the one composition: the room reads a `ResolvedRoom`, the bidder is
#: unauthenticated and cannot. Named here so a third surface that grows its own factory shows
#: up as a count mismatch rather than as silence.
FACTORIES = ("session_for", "session_from")


@pytest.mark.parametrize("factory", FACTORIES)
def test_each_live_command_composes_with_the_timed_journal(factory: str) -> None:
    """The join between the two halves above: the lifted loop journals through whatever it
    was given, so the row carries `cycle_ms` only if the command handed it the timed sink.

    `session_for(journal=journal)` — the raw one — would leave every waiting and error row
    with no timing at all, which is the defect this whole file is about, moved one layer out
    rather than fixed. Both surfaces, because 3.9a gave `asta bid` the same seam to miss.
    """
    [journal] = [
        keyword.value
        for call in _calls_named(_tree(), factory)
        for keyword in call.keywords
        if keyword.arg == "journal"
    ]

    assert isinstance(journal, ast.Name) and journal.id == "_timed_journal", (
        f"`{factory}` journals through `{ast.unparse(journal)}`, not the timed sink"
    )


def test_both_factories_are_actually_used() -> None:
    """The parametrisation above is over a written list, so it cannot notice a door nobody
    opens — and a `[journal] = [...]` over an empty list raises `ValueError`, not an
    assertion anybody reads as "this command stopped composing a session"."""
    used = {
        name for name in FACTORIES if _calls_named(_tree(), name)
    }
    assert used == set(FACTORIES), f"unused session factory: {sorted(set(FACTORIES) - used)}"


def test_the_clock_starts_in_the_read_and_nowhere_else() -> None:
    """One start per command, and it is the loop's first act each poll.

    Two starts would be two meanings for one field. A start inside `target_of` is the one
    that leaves a waiting poll with no clock at all — which is where this began.
    """
    tree = _tree()
    starts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id == "cycle_started"
    ]
    assert len(starts) == 2, f"expected one clock start per command, found {len(starts)}"

    # The *innermost* enclosing function, not every one: `asta_room` contains
    # `_timed_read`, so a plain containment walk reports the command as a clock site too
    # and the assertion below would be about nothing.
    def innermost(start: ast.AST) -> str:
        holders = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(child is start for child in ast.walk(node))
        ]
        return max(holders, key=lambda node: node.lineno).name

    enclosing = {innermost(start) for start in starts}
    assert enclosing == {"_timed_read"}, (
        f"the poll clock is started inside {sorted(enclosing)}; it must be the read, which "
        "is the loop's first act each poll"
    )


def test_the_loops_read_through_the_timed_read() -> None:
    """A `read=` that bypasses it is a poll whose clock never started, so every row that
    poll writes reports the time since the *previous* one."""
    tree = _tree()
    # Found by the keyword rather than by the callee's name: since 3.6b the room drives
    # `AstaSession.run` and `asta bid` still calls `run_bid_loop` directly, and a scan keyed
    # to either name would silently cover one command. `poll_seconds` is what both loop
    # drivers take and nothing else in this module does — `LotRouter(read=, write=)` is the
    # near miss a `read=`-only scan would pick up.
    loops = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and any(keyword.arg == "poll_seconds" for keyword in call.keywords)
    ]
    reads = [
        keyword.value for call in loops for keyword in call.keywords if keyword.arg == "read"
    ]

    assert len(reads) == 2, f"expected two bid loops, found {len(reads)}"
    for value in reads:
        assert isinstance(value, ast.Name) and value.id == "_timed_read", (
            "a bid loop reads through something other than _timed_read"
        )


def test_the_journal_reader_has_a_field_for_it() -> None:
    """The other end. `read_rows` is what a viewer uses, and a field nobody reads back is
    the `bargain_spent` drift again."""
    from fantabot.adapters.files.room_journal import ROW_FIELDS, JournalEntry

    assert "cycle_ms" in ROW_FIELDS
    assert JournalEntry(index=1).cycle_ms is None
