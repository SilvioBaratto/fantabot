"""One-off collection of the Mantra tactical grid.

The 11 schemas and the per-formation out-of-position matrix are what a Mantra
lineup engine needs and this repo does not have. ``rules/sistema-mantra.md``
carries the general rules but says plainly that the full compatibility table "is
a separate download that isn't captured here".

Run once, verified by hand, committed. Deliberately **not** on cron: these are
fixed rules, not moving data, and a silent weekly re-collection is precisely how
a bad transcription would slip in unnoticed.
"""
