"""fantabot-app — a local, user-friendly web UI over fantabot.

One command boots a single process that provisions Postgres (no Docker), runs the
FastAPI adapter, and serves the compiled Angular bundle. See ``app/CLAUDE.md``; closed phases are
archived under ``tasks/archive/`` (git-ignored, maintainer-only).
"""

__version__ = "0.1.0"
