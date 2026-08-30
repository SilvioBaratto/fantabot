"""Read-side adapters over the data the decision layers consume.

Today that is one module: :mod:`news_sentiment`, the read side of
``fantabot news fetch``, plus the frozen value types it serves in
:mod:`models`.

**What used to be here.** A ``StatsSource`` Protocol — ``projected_scores`` /
``player_pool`` / ``target_price`` — declared against a per-matchday stats
provider that was never chosen. It was removed with the Classic lineup
scaffolding it was written for (``lineup.py``, ``auction.py``, ``strategy.py``):
an interface with no implementation and no caller is a guess about a shape, and
this one had been guessed three phases before anything would consume it. The
asta engine does not need it — it prices from ``quotazioni.fvm``, the observed
clearing prices in ``asta_assignment`` and the sentiment feed, none of which
that Protocol described.

When a per-matchday stats source is picked, the interface gets written against
the consumer that actually exists at the time.
"""
