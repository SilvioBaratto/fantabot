"""fantabot-app — a local, user-friendly web UI over fantabot.

One command boots a single process that provisions Postgres (no Docker), runs the
FastAPI adapter, and serves the compiled Angular bundle. See ``app/CLAUDE.md``; the
closed phases' specs are cited by phase name, because they are archived outside this
checkout.
"""

__version__ = "0.1.0"
