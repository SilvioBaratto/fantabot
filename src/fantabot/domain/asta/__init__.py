"""The asta engine: roster legality (L1), value, the optimizer, the advisory loop.

Advisory MVP for Mantra lega 4103937. Everything here is a decision -- what a player is
worth, which XI is legal, what to bid -- and nothing here does I/O, which is why the
whole surface is testable without a database or a socket.

Its edges are elsewhere: `application/asta_planner.py` reads the two tables and assembles
the value model, `adapters/http/fantalab/` reads the live room, and `interface/asta.py` is
the command. See ``docs/spec-asta-copilota.md`` and ``tasks/archive/asta-copilota-plan.md``.
"""
