"""Classic (P/D/C/A) format domain — the counterpart to the Mantra engine.

Pure. Where Mantra carries 12 granular role codes, a frozenset per player, and legality is a
bipartite match over 11 schemi (`domain/asta/legality.py`), Classic carries four macro roles,
**one per player**, and legality is counting — `have >= count in each of {P,D,C,A}`. Kept a
separate seam from the Mantra matcher on purpose (SPEC "hybrid"): counting and matching are
different algorithms, and forcing either through the other's machinery buys nothing.
"""
