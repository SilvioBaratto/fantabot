"""The asta engine: roster legality (L1), value, optimizer, and the live advisory loop.

Advisory MVP for Mantra lega 4103937. Decision logic is pure and testable; the I/O shell
(DB reads, the live room feed, skfolio) is confined to the modules that name it. See
``docs/spec-asta-copilota.md`` and ``tasks/archive/asta-copilota-plan.md``.
"""
