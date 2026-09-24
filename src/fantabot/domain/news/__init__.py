"""Weekly per-player news sentiment: the pure half.

One agent query per player over WebSearch/WebFetch, validated against
:class:`~fantabot.domain.news.models.PlayerSentiment`. The pool join, the prompt, the
Mantra drift calculation and the row flattening live here. The fan-out itself does not:
it moved to `application/news_fetcher.py` with the layering, and this said it lived here
until 2026-09-24. The argument it was making still holds there — `fetch_all` returns rows
rather than storing them, and a test forbids the persistence package from appearing in it
at all, which is what lets the concurrency cap, the backoff and the failure isolation be
tested with fakes.

The one read is `adapters/persistence/news_pool.py`; the sink and the command are in
`application/` and `interface/`.
"""
