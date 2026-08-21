"""Weekly per-player news sentiment.

One agent query per player over WebSearch/WebFetch, validated against
:class:`~fantabot.news.models.PlayerSentiment`, appended to
``data/player_sentiment_2026-27.csv`` as a per-player time-series.

The modules here are split by whether they touch the outside world. ``models``,
``mantra``, ``prompt`` and ``pool`` are pure and carry the whole decision surface;
``store`` and ``pipeline`` do the I/O. That is the same property that made
``strategy.py`` the only tested module in this repo before now, and it is what
lets this suite run without a single agent call.
"""
