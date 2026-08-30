"""Weekly per-player news sentiment: the pure half.

One agent query per player over WebSearch/WebFetch, validated against
:class:`~fantabot.domain.news.models.PlayerSentiment`. The pool join, the prompt, the
Mantra drift calculation, the row flattening and the fan-out itself all live here --
`pipeline.fetch_all` returns rows rather than storing them, and a test forbids the
persistence package from appearing in it at all, which is what lets the concurrency cap,
the backoff and the failure isolation be tested with fakes.

The one read is `adapters/persistence/news_pool.py`; the sink and the command are in
`application/` and `interface/`.
"""
