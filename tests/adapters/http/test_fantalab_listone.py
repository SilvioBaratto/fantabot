"""The listone cache envelope, an injectable transport, and the age/version it carries.

The bridge from FantaLab's player UUIDs to fantacalcio ids is a plain file because a live
room does not want an HTTP round trip it can avoid — but a file with no timestamp and no
version has no way to say it has gone stale (SPEC's defect E). This is the fix, tested
without a socket: `httpx.MockTransport` fakes the network the same way `rest.py`'s tests do.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from fantabot.adapters.http.fantalab import listone

PAYLOAD = {
    "season": "2026/27",
    "players": [
        {"player_id": "uuid-a", "fantacalcio_id": 100, "name": "Uno", "team_name": "AAA"},
        {"player_id": "uuid-b", "fantacalcio_id": 200, "name": "Due", "team_name": "BBB"},
        {"player_id": "no-id"},
    ],
}


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=PAYLOAD)


class TestEntriesOnly:
    """The envelope's metadata sits *alongside* the entries, not nested — so telling one from
    the other is a structural check (a metadata value is never a `Mapping`), not a hand-kept
    list of the four key names."""

    def test_envelope_keys_are_filtered_out(self) -> None:
        raw = {
            "version": 1, "count": 1, "season": "2026/27", "fetched_at": "2026-09-02T00:00:00",
            "uuid-a": {"fantacalcio_id": 100},
        }

        assert listone.entries_only(raw) == {"uuid-a": {"fantacalcio_id": 100}}

    def test_a_bare_pre_version_file_is_unaffected(self) -> None:
        """Every entry is a `Mapping` and there is no envelope at all — the common case for
        every cache file written before this existed."""
        raw = {"uuid-a": {"fantacalcio_id": 100}, "uuid-b": {"fantacalcio_id": 200}}

        assert listone.entries_only(raw) == raw

    def test_an_entirely_empty_payload_is_an_empty_mapping(self) -> None:
        assert listone.entries_only({}) == {}


class TestFromCacheToleratesTheEnvelope:
    def test_a_versioned_cache_still_yields_the_bridge(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "listone_map.json"
        path.write_text(
            json.dumps({
                "version": 1, "count": 1, "season": "2026/27",
                "fetched_at": "2026-09-02T00:00:00+00:00",
                "uuid-a": {"fantacalcio_id": 512, "name": "Someone"},
            }),
            encoding="utf-8",
        )

        assert listone.from_cache(path) == {"uuid-a": 512}


class TestCacheVersion:
    def test_a_versioned_file_reports_its_version(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "listone_map.json"
        path.write_text(json.dumps({"version": 1, "uuid-a": {"fantacalcio_id": 1}}), encoding="utf-8")

        assert listone.cache_version(path) == 1

    def test_a_pre_version_file_reports_none(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "listone_map.json"
        path.write_text(json.dumps({"uuid-a": {"fantacalcio_id": 1}}), encoding="utf-8")

        assert listone.cache_version(path) is None

    def test_a_missing_file_reports_none_rather_than_raising(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert listone.cache_version(tmp_path / "nope.json") is None


class TestCacheAge:
    def test_a_fresh_fetch_reports_a_small_age(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        now = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
        path = tmp_path / "listone_map.json"
        path.write_text(
            json.dumps({"version": 1, "fetched_at": (now - timedelta(seconds=5)).isoformat()}),
            encoding="utf-8",
        )

        assert listone.cache_age(path, now=now) == 5.0

    def test_a_pre_version_file_with_no_fetched_at_reports_none(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "listone_map.json"
        path.write_text(json.dumps({"uuid-a": {"fantacalcio_id": 1}}), encoding="utf-8")

        assert listone.cache_age(path) is None

    def test_a_missing_file_reports_none_rather_than_raising(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assert listone.cache_age(tmp_path / "nope.json") is None

    def test_an_unparseable_timestamp_reports_none_rather_than_raising(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "listone_map.json"
        path.write_text(json.dumps({"version": 1, "fetched_at": "not-a-timestamp"}), encoding="utf-8")

        assert listone.cache_age(path) is None


class TestFetchWithAnInjectableTransport:
    def test_refresh_true_hits_the_network_even_with_a_populated_cache(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cache = tmp_path / "listone_map.json"
        cache.write_text(json.dumps({"uuid-stale": {"fantacalcio_id": 999}}), encoding="utf-8")

        bridge = listone.fetch(cache, refresh=True, transport=httpx.MockTransport(_handler))

        assert bridge == {"uuid-a": 100, "uuid-b": 200}

    def test_the_cache_is_written_with_the_versioned_envelope(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cache = tmp_path / "listone_map.json"

        listone.fetch(cache, refresh=True, transport=httpx.MockTransport(_handler))
        written = json.loads(cache.read_text(encoding="utf-8"))

        assert written["version"] == listone.CACHE_VERSION
        # 3, not 2: `count` is how many entries were written, and `fetch` writes one even for
        # `no-id` (a player with no `fantacalcio_id`) — `from_cache`/`parse` are what filter
        # those out again at read time, unchanged by this task.
        assert written["count"] == 3
        assert written["season"] == "2026/27"
        assert isinstance(written["fetched_at"], str)
        assert written["uuid-a"]["fantacalcio_id"] == 100

    def test_a_populated_cache_is_used_without_touching_the_network(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("the network must not be reached when the cache has data")

        cache = tmp_path / "listone_map.json"
        cache.write_text(
            json.dumps({"version": 1, "uuid-a": {"fantacalcio_id": 100}}), encoding="utf-8"
        )

        bridge = listone.fetch(cache, transport=httpx.MockTransport(explode))

        assert bridge == {"uuid-a": 100}


class TestATransportFailureDegradesToTheCache:
    """`refresh=True` asks for a live read, not a fatal one — the room must not crash on a
    flaky link at exactly the moment it is trying to be extra sure the bridge is current.
    """

    @staticmethod
    def _unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    def test_a_connect_error_falls_back_to_the_cache_even_with_refresh_true(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cache = tmp_path / "listone_map.json"
        cache.write_text(
            json.dumps({"version": 1, "uuid-a": {"fantacalcio_id": 100}}), encoding="utf-8"
        )

        bridge = listone.fetch(
            cache, refresh=True, transport=httpx.MockTransport(self._unreachable)
        )

        assert bridge == {"uuid-a": 100}

    def test_the_fallen_back_to_cache_is_left_exactly_as_it_was(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """No write on failure — `cache_age` afterward has to report the *old* fetch's age,
        not a fresh timestamp stamped on a read that never actually happened."""
        cache = tmp_path / "listone_map.json"
        before = cache_before = json.dumps(
            {"version": 1, "fetched_at": "2020-01-01T00:00:00+00:00", "uuid-a": {"fantacalcio_id": 100}}
        )
        cache.write_text(before, encoding="utf-8")

        listone.fetch(cache, refresh=True, transport=httpx.MockTransport(self._unreachable))

        assert cache.read_text(encoding="utf-8") == cache_before

    def test_no_cache_and_no_network_is_an_empty_bridge_not_a_raised_exception(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        cache = tmp_path / "nope.json"

        bridge = listone.fetch(cache, refresh=True, transport=httpx.MockTransport(self._unreachable))

        assert bridge == {}


class TestIsStale:
    """The arm-refusal decision, extracted so it is tested without a CLI harness — `asta
    room`/`asta bid` both call this instead of hand-rolling the comparison."""

    def test_older_than_the_limit_is_stale(self) -> None:
        assert listone.is_stale(5 * 3600, max_hours=4.0) is True

    def test_younger_than_the_limit_is_not_stale(self) -> None:
        assert listone.is_stale(3 * 3600, max_hours=4.0) is False

    def test_exactly_the_limit_is_not_yet_stale(self) -> None:
        assert listone.is_stale(4 * 3600, max_hours=4.0) is False

    def test_an_unknown_age_is_never_stale(self) -> None:
        """A pre-envelope cache, or a fetch that never ran — refusing to arm on missing
        information a room never had the chance to write would punish the upgrade itself."""
        assert listone.is_stale(None, max_hours=4.0) is False
