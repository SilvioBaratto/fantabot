"""Scrapers over `fantacalcio.it`'s public pages.

These fetch from the site and write through `db/scraping.py`. They lived in
`scripts/` as standalone command-line programs until 2026-08-30 — outside `ruff`,
outside `mypy`
and outside the test suite, which is the whole reason they moved.

Each exposes a `run(...)` taking real arguments; `interface`-side Typer commands in
the `db` group supply them. Nothing here builds a parser or reads `sys.argv`, so the
same function is callable from a test.
"""
