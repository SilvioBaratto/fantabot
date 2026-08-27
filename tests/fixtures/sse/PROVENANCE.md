# SSE fixture provenance

Recorded 2026-08-27 from auction `959f67a2` (shard `fantalab-4`, Classic 4×500),
the only auction live at the time. 100 seconds, 5 transport chunks, 845 bytes.

| File | Origin |
|---|---|
| `live_auction.txt` | **real** — the capture verbatim: one `put`, two `patch`, two keep-alives |
| `keepalive.txt` | **real** — a keep-alive lifted from the same capture, plus the `put` for context |
| `null_patch.txt` | **synthetic** — shape observed on 2026-08-27 during the SSE spike; the capture caught a bid war rather than a close, so no real one is in hand. Replace it the first time a close is recorded. |
| `split_frame.json` | **synthetic** — a real frame cut in half. The transport delivered whole frames in this capture; chunk boundaries are not guaranteed to fall there, and the parser must not assume they do. |

## What the capture showed that we had wrong

**The keep-alive is an event, not an SSE comment.**

```
event: keep-alive
data: null
```

The test asserted a leading `:`, the SSE convention. Firebase does not use it. The
assertion was corrected against the recording rather than the other way round.

The capture also caught a live bid war — `price` 261 → 262 → 366 in 62 seconds —
which is the intermediate detail polling collapses and this collector exists to keep.
