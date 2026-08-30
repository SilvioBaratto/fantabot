"""Parsing Firebase's SSE stream into frames.

Everything here replays bytes recorded from a live auction. The recording is
what corrected the first draft of these tests: Firebase's keep-alive is
`event: keep-alive` with `data: null`, not the SSE comment line the convention
would suggest, and a parser built on the convention would have treated real
events as noise.
"""

from __future__ import annotations

import json

from _paths import SSE_FIXTURES

from fantabot.aste.sse import FrameBuffer, parse

SSE = SSE_FIXTURES
LIVE = (SSE / "live_auction.txt").read_text(encoding="utf-8")


def test_the_recorded_stream_parses_to_its_events() -> None:
    kinds = [frame.event for frame in parse(LIVE)]
    assert kinds == ["put", "keep-alive", "patch", "patch", "keep-alive"]


def test_a_put_carries_the_whole_node() -> None:
    put = parse(LIVE)[0]
    assert put.path == "/"
    assert put.data["price"] == 261
    assert put.data["update_type"] == "raise"


def test_a_keepalive_carries_no_data() -> None:
    """It must not be mistaken for a patch that deletes everything."""
    keepalive = parse(LIVE)[1]
    assert keepalive.event == "keep-alive"
    assert keepalive.data is None


def test_a_frame_split_across_chunks_is_reassembled() -> None:
    """Transport boundaries fall anywhere. A parser that assumes one frame per
    read silently drops the tail — and silence is the failure mode that costs a
    night of collection."""
    chunks = json.loads((SSE / "split_frame.json").read_text(encoding="utf-8"))
    buffer = FrameBuffer()
    frames = [frame for chunk in chunks for frame in buffer.feed(chunk)]
    assert len(frames) == 1
    assert frames[0].event == "put"
    assert frames[0].data["price"] == 261


def test_an_incomplete_tail_is_held_not_emitted() -> None:
    buffer = FrameBuffer()
    assert buffer.feed("event: put\ndata: {\"path\":\"/\",\"data\":{}}") == []
    assert len(buffer.feed("\n\n")) == 1


def test_feeding_the_whole_recording_one_byte_at_a_time_is_equivalent() -> None:
    """The strongest statement of chunk-independence available: if any boundary
    breaks the parser, one of these 845 does."""
    buffer = FrameBuffer()
    streamed = [frame for byte in LIVE for frame in buffer.feed(byte)]
    assert [f.event for f in streamed] == [f.event for f in parse(LIVE)]


def test_an_unparseable_payload_does_not_end_the_stream() -> None:
    """A gateway error page is not JSON. Raising here would end one auction's
    watch for the night, which is exactly the silent death this phase was
    written to stop repeating."""
    frames = parse("event: patch\ndata: <html>502</html>\n\n" + LIVE)
    assert [f.event for f in frames] == ["put", "keep-alive", "patch", "patch", "keep-alive"]


def test_a_dropped_frame_is_counted_not_hidden() -> None:
    """Dropping an unparseable block is right; dropping it silently is not. A
    supervisor that never sees this move cannot tell a quiet auction from a
    stream that has been serving HTML for ten minutes."""
    buffer = FrameBuffer()
    buffer.feed("event: patch\ndata: <html>502</html>\n\n")
    assert buffer.malformed == 1
    buffer.feed(LIVE)
    assert buffer.malformed == 1, "valid frames must not inflate the count"


def test_parse_discards_an_unterminated_tail_as_its_docstring_says() -> None:
    """`parse` split on the separator and parsed the final element like any
    other, so an unterminated frame was *emitted* — the opposite of the claim,
    and the opposite of what `FrameBuffer` does with the same bytes."""
    text = 'event: patch\ndata: {"path":"/","data":{"price":999}}'  # no trailing blank line
    assert parse(text) == []


def test_the_two_entry_points_agree_on_a_stream_cut_mid_frame() -> None:
    """The chunk-independence test replays a recording that ends exactly on a
    separator — the one input where these two cannot disagree. That made `parse`
    a false oracle: correct only by accident of the fixture.

    The transport gives no guarantee about where a stream stops, which is the
    module's own stated reason for `FrameBuffer` existing.
    """
    truncated = LIVE[:-1]
    buffer = FrameBuffer()
    streamed = [frame for byte in truncated for frame in buffer.feed(byte)]
    assert [f.event for f in streamed] == [f.event for f in parse(truncated)]
