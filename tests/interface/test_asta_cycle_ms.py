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

**Two modules, since 3.6b.** The room's loop was lifted into `application/asta_session.py`,
and both rows went with it — so a scan of the Typer body alone would now find `asta bid`'s
two, report success on half the surface, and say nothing about the one that moved. The
clock itself did **not** move: `cycle_ms` is still measured in `interface/`, around the
journal the session is handed, for the same reason `interface/asta.py::_today` is the asta
feature's only calendar read.
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

    Two rows each on two surfaces: `asta bid`'s pair is still inline in the Typer body, and
    the room's pair moved into `AstaSession.run` with the loop that produces them.
    """
    assert len(_row_builder_calls(_tree())) == 2, "asta bid's two rows"
    assert len(_row_builder_calls(_tree(SESSION))) == 2, "the lifted loop's two rows"


@pytest.mark.parametrize("module", [ASTA, SESSION])
@pytest.mark.parametrize("builder", ["waiting_row", "error_row"])
def test_every_row_goes_through_the_timed_journal(builder: str, module: str) -> None:
    """`journal.write(waiting_row(...))` is the defect. The timed sink is the fix.

    In the Typer body that sink is `_timed_journal`; in `AstaSession.run` it is whatever
    the caller injected — `_timed_journal` for the room, asserted below.
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


@pytest.mark.parametrize(
    ("module", "sink"), [(ASTA, "_timed_journal"), (SESSION, "_journal")], ids=["cli", "session"]
)
@pytest.mark.parametrize("builder", ["waiting_row", "error_row"])
def test_and_reaches_the_timed_one(builder: str, module: str, sink: str) -> None:
    """One per surface. `asta bid` calls `_timed_journal` by name; the lifted loop calls the
    journal it was composed with, which for the room is that same function."""
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


def test_the_rooms_session_is_composed_with_the_timed_journal() -> None:
    """The join between the two halves above: the lifted loop journals through whatever it
    was given, so the row carries `cycle_ms` only if the room handed it the timed sink.

    `session_for(journal=journal)` — the raw one — would leave every waiting and error row
    the room writes with no timing at all, which is the defect this whole file is about,
    moved one layer out rather than fixed.
    """
    [journal] = [
        keyword.value
        for call in _calls_named(_tree(), "session_for")
        for keyword in call.keywords
        if keyword.arg == "journal"
    ]

    assert isinstance(journal, ast.Name) and journal.id == "_timed_journal", (
        f"the room's session journals through `{ast.unparse(journal)}`, not the timed sink"
    )


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
