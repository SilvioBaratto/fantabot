"""The code's side of the schema comparison: what the migration scripts say the head is.

Read through alembic's own `ScriptDirectory`, so what the database is held to is what
`alembic upgrade head` would actually apply — not a second parser of revision files that
could come to disagree with it. Nothing here touches the database.

**The locator is shared with the app's migration runner.** `fantabot-app setup` upgrades
the schema through `fantabot_app.provisioner.migrate`, and a schema check that looked in a
different place would be comparing the database against migrations the upgrade never runs.
That module asks `alembic_ini_path`; this one is the only place the path is derived.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fantabot.domain.shared.schema_revision import ScriptRevisions

if TYPE_CHECKING:
    from alembic.script import ScriptDirectory


def alembic_ini_path() -> Path:
    """Where `alembic.ini` is in a source checkout: the repository root.

    `src/fantabot/__init__.py` is two levels below it. The path is returned whether or not
    the file exists — an installed wheel has no `alembic.ini` beside it, and what to do
    about that is the caller's decision (`load_script_revisions` reports it as
    unavailable; the migration runner lets alembic say so).
    """
    import fantabot

    return Path(fantabot.__file__).resolve().parents[2] / "alembic.ini"


def load_script_revisions(ini: Path | None = None) -> ScriptRevisions | None:
    """The head(s) and every known revision of the migration scripts, or `None`.

    `None` means the scripts are not there to be read — no `alembic.ini`, or one whose
    script directory is gone — which is an answer about the install and not an error: a
    wheel carries no migrations beside it. A migration file that fails to import is a
    defect in the checkout and propagates, as it would from `alembic` itself.

    Args:
        ini: The `alembic.ini` to read. Defaults to this checkout's own.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util import CommandError

    path = ini if ini is not None else alembic_ini_path()
    # No `is_file` guard of its own: a missing file reads as a config with no
    # `script_location`, which alembic reports as the same `CommandError` a missing script
    # directory does, so one handler answers both and a second check could never be observed.
    #
    # `from_config` prepends the ini's `prepend_sys_path` — a cwd-relative `src` — to
    # `sys.path`. Reading the head must not change what the process goes on to import.
    saved = list(sys.path)
    try:
        script = ScriptDirectory.from_config(Config(str(path)))
        heads = tuple(script.get_heads())
        pending = {
            revision.revision: _pending(script, revision.revision)
            for revision in script.walk_revisions()
        }
    except CommandError:
        return None
    finally:
        sys.path[:] = saved
    return ScriptRevisions(heads=heads, pending=pending)


def _pending(script: ScriptDirectory, revision: str) -> int | None:
    """How many migrations `upgrade head` would apply from `revision`, or `None`.

    `None` for a revision on a branch the walk cannot order against every head, which
    alembic reports as an error; the revision is still *known*, it just has no honest count.
    """
    from alembic.script.revision import RevisionError
    from alembic.util import CommandError

    try:
        return len(list(script.iterate_revisions("heads", revision)))
    except (CommandError, RevisionError):
        return None
