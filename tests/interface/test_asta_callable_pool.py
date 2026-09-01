"""The planner plans over players the room can actually call.

`asta bid` narrowed its pool to FantaLab's listone; `asta optimize` did not — and it is the
command whose output an operator reads the night before and bids from by hand. Measured
2026-09-01 on the live pool: 41 of 570 players are absent from the listone, and the printed
30-man plan contained Lukaku (2531), priced at fvm 41 in `quotazioni` and absent from the
listone, so the room can never call him. The plan therefore had 29 fillable slots and one
that could not be filled, and said `30 players`.

The failure mode of the fix is the interesting half. `read_plan_inputs` reads an empty
`callable_ids` as a real, total exclusion — correct for the bidder, where an unresolvable
bridge means every lot would be unknown — and it would empty the pool here. So "the listone
is unreachable" must arrive as `None` (do not filter) and never as `set()`, or a CDN outage
silently turns the night-before plan into `InfeasibleRoster`.
"""

from __future__ import annotations

import httpx

from fantabot.interface import asta


class _Boom(httpx.ConnectError):
    pass


def _raise(exc: Exception):
    def fetch() -> dict[str, int]:
        raise exc

    return fetch


class TestCallableIds:
    def test_the_bridge_becomes_string_fantacalcio_ids(self) -> None:
        """`listone.parse` keeps them as `int` while pool ids are `str`; an unconverted set
        matches nothing, and the filter would empty the pool instead of narrowing it."""
        notes: list[str] = []
        ids = asta._callable_ids(notes.append, _fetch=lambda: {"uuid-a": 100, "uuid-b": 200})

        assert ids == {"100", "200"}
        assert notes == []

    def test_an_unreachable_listone_disables_the_filter_rather_than_emptying_the_pool(
        self,
    ) -> None:
        notes: list[str] = []
        ids = asta._callable_ids(notes.append, _fetch=_raise(_Boom("down")))

        # `None`, emphatically not `set()`: the second means "nobody can be called".
        assert ids is None
        assert len(notes) == 1
        assert "whole pool" in notes[0]

    def test_an_empty_bridge_also_disables_the_filter(self) -> None:
        notes: list[str] = []
        ids = asta._callable_ids(notes.append, _fetch=dict)

        assert ids is None
        assert notes == ["listone empty; planning over the whole pool"]

    def test_the_operator_is_always_told_when_the_filter_is_off(self) -> None:
        """Silence here restates the whole bug: a plan over the wider pool that reads exactly
        like a plan over the narrower one."""
        for fetch in (_raise(_Boom("down")), dict):
            notes: list[str] = []
            asta._callable_ids(notes.append, _fetch=fetch)
            assert notes, "a disabled filter must be announced"

    def test_a_timeout_degrades_the_same_way_as_a_refusal(self) -> None:
        """The except clause is broad on purpose — every transport failure means the same
        thing here, and a narrow one would let a `ReadTimeout` end the command."""
        notes: list[str] = []
        assert asta._callable_ids(notes.append, _fetch=_raise(httpx.ReadTimeout("slow"))) is None
        assert "ReadTimeout" in notes[0]
