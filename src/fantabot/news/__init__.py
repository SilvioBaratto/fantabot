"""Weekly per-player news sentiment.

One agent query per player over WebSearch/WebFetch, validated against
:class:`~fantabot.news.models.PlayerSentiment`, appended to
``data/player_sentiment_2026-27.csv`` as a per-player time-series.

The modules here are split by whether they touch the outside world. ``models``,
``mantra``, ``prompt`` and ``pool`` are pure and carry the whole decision surface;
``store`` and ``pipeline`` do the I/O. That separation is what lets this suite run
without a single agent call — the same property the whole repository is built on.
"""
