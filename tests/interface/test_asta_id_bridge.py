"""FantaLab UUIDs become fantacalcio ids before they reach the engine.

Two defects lived in the gap, and both were silent. `asta bid` raised
`InfeasibleRoster` on the first lot it won, because the UUID it put into
`AstaState.owned` is not in a pool keyed by fantacalcio id. And `taken` never
matched anything at all, so every player an opponent had already bought stayed
available to the planner — measured on the replay fixture, 0 of 18 sales excluded
before, 18 of 18 after.

The second one is the reason a crash was the lucky outcome: it failed loudly.
`taken` failed quietly, and the advisory that came out named players who were gone.
"""

from __future__ import annotations

from fantabot.adapters.http.fantalab import listone
from fantabot.domain.asta.live import AssignmentEvent, resolve_ids
from fantabot.interface.asta import _target_of


def _event(player_id: str, price: int = 10) -> AssignmentEvent:
    return AssignmentEvent(player_id=player_id, price=price, buyer_team_id="t1")


class TestResolveIds:
    def test_it_rekeys_events_to_fantacalcio_ids(self) -> None:
        resolved, unknown = resolve_ids([_event("uuid-a"), _event("uuid-b")],
                                        {"uuid-a": 100, "uuid-b": 200})

        assert [e.player_id for e in resolved] == ["100", "200"]
        assert unknown == []

    def test_everything_else_about_the_event_survives(self) -> None:
        (resolved,), _ = resolve_ids([_event("uuid-a", price=42)], {"uuid-a": 100})

        assert resolved.price == 42
        assert resolved.buyer_team_id == "t1"

    def test_an_unmappable_player_is_dropped_and_counted(self) -> None:
        """Counted, because a drop nobody counts reads as an empty input.

        Keeping him would put the same unmappable id into `owned` by a longer route,
        which is the crash this exists to prevent.
        """
        resolved, unknown = resolve_ids([_event("known"), _event("stranger")],
                                        {"known": 7})

        assert [e.player_id for e in resolved] == ["7"]
        assert unknown == ["stranger"]

    def test_an_empty_bridge_drops_everything_rather_than_passing_uuids_through(self) -> None:
        """The failure mode that must not be silent.

        Passing UUIDs through on an empty bridge is exactly the old behaviour, and it
        is why `asta bid` refuses to start without one.
        """
        resolved, unknown = resolve_ids([_event("a"), _event("b")], {})

        assert resolved == []
        assert len(unknown) == 2


class TestListoneParsing:
    def test_it_keeps_only_players_with_an_integer_id(self) -> None:
        """A missing `fantacalcio_id` means "cannot be valued", not "id is None".

        Mapping it to `None` would make the pool lookup fail on a value rather than
        on an absence, which reads as a different bug.
        """
        table = listone.parse(
            {
                "players": [
                    {"player_id": "a", "fantacalcio_id": 1},
                    {"player_id": "b", "fantacalcio_id": None},
                    {"player_id": "c"},
                    {"fantacalcio_id": 4},
                ]
            }
        )

        assert table == {"a": 1}

    def test_an_empty_payload_is_an_empty_mapping_not_an_error(self) -> None:
        assert listone.parse({}) == {}

    def test_a_missing_cache_is_empty_rather_than_a_crash(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert listone.from_cache(tmp_path / "nope.json") == {}

    def test_a_malformed_cache_is_empty_rather_than_a_crash(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert listone.from_cache(bad) == {}

    def test_the_cache_reads_the_shape_the_harvest_side_writes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """One mapping, one file. Two caches of it would be two things to go stale."""
        import json

        path = tmp_path / "listone_map.json"
        path.write_text(
            json.dumps({"uuid-a": {"fantacalcio_id": 512, "name": "Someone"}}),
            encoding="utf-8",
        )

        assert listone.from_cache(path) == {"uuid-a": 512}


class TestTargetOf:
    """The third gap of the same kind, and the one that made `asta bid` bid nothing.

    `resolve_ids` re-keys the *ledger*, so `AstaState.owned` and every walk-away are
    fantacalcio ids. The lot on the block arrives from a different place — the raw
    ``auction/<fl>`` snapshot — and is still a FantaLab UUID. Looking one up among the
    others misses on every lot, and the loop answers "not a target, hold" all evening:
    no bid, no error, no traceback. The two defects in this file's docstring were a
    crash and a quiet wrong answer; this one is a quiet *absence* of answers.
    """

    def test_the_lot_uuid_is_translated_before_the_walkaway_lookup(self) -> None:
        target = _target_of({"player_id": "uuid-a"}, {"uuid-a": 100}, {"100": 42.7})

        assert target is not None
        node_id, walk_away = target
        assert node_id == "uuid-a", "the payload has to name the lot the way the node does"
        assert walk_away == 42, "priced on the fantacalcio id, truncated to whole credits"

    def test_a_uuid_the_bridge_does_not_know_is_not_a_target(self) -> None:
        assert _target_of({"player_id": "stranger"}, {"uuid-a": 100}, {"100": 42.7}) is None

    def test_a_known_player_we_have_no_walkaway_for_is_not_a_target(self) -> None:
        """The plan prices its own targets only; everything else is a lot we let go."""
        assert _target_of({"player_id": "uuid-a"}, {"uuid-a": 100}, {"999": 42.7}) is None

    def test_a_snapshot_with_no_lot_is_not_a_target(self) -> None:
        assert _target_of({}, {"uuid-a": 100}, {"100": 42.7}) is None
        assert _target_of({"player_id": None}, {"uuid-a": 100}, {"100": 42.7}) is None

    def test_a_zero_walkaway_is_still_a_target_and_the_refusal_is_the_engines(self) -> None:
        """`decide_bid` owns "too expensive", not the translation. Returning `None` here
        would make a priced-at-zero player indistinguishable from an unknown one, and the
        heartbeat could no longer tell the operator which of the two it saw."""
        assert _target_of({"player_id": "uuid-a"}, {"uuid-a": 100}, {"100": 0.0}) == ("uuid-a", 0)
