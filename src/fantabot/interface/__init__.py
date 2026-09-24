"""The CLI layer: Typer commands, and the objects every command shares.

Eight modules live here. Five hold commands: `app` — the root, and where the seven
groups are declared — and `asta`, `harvest`, `lega` and `lineup`, each registering
onto a group rather than decorating. Three hold none: `console`, which owns the one
`Console` in the package; `options`, the option declarations more than one command
needs; and `room_view`, the live room's rendering. This said "two modules live here
today" until 2026-09-24, when it was describing only `console` and `options` — and
both are still here for the reason it gave, that the alternative was a circular import.
"""
