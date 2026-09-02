"""Domain of the weekly Mantra formazione: build the best legal XI and submit it.

Pure, like the rest of `domain/` — no I/O, no clock, no network, no framework import. The
value model, the schema slot tables, the weighted assignment and the payload all live here;
the `gaming/v1` calls live in `adapters/http/apileague`, and the orchestration in
`application/lineup_planner`. See `SPEC.md` (phase `lineup`).
"""
