"""`python -m fantabot` — the CLI reached by interpreter rather than by `PATH`.

The `fantabot` console script is on `PATH` only while the CLI's own environment is
active. The app runs in a *different* virtualenv, and its supervisor spawns the CLI as a
subprocess: `[sys.executable, "-m", "fantabot", ...]` names the interpreter it is already
running under and needs nothing on `PATH` at all.

Deliberately three lines. Anything that has to happen before the CLI runs belongs in
`interface/app.py`, which the console script also goes through — a `__main__` that did
more would make the two entry points differ exactly where nobody looks.
"""

from fantabot.interface.app import app

if __name__ == "__main__":
    app()
