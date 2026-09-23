"""One containment boundary, used by every optional step. SPEC A19(3).

The lineup job has two halves. The **submit** is the job: it fields an XI every hour and a
failure there is a matchday lost. The **optional** half — the shadow plan, each refresh
source — is worth having and worth nothing if it can take the submit down with it. So every
optional step runs inside this, and what comes out is a value and a reason rather than an
exception.

Two names pass through, and only two:

* **`AssertionError`** is a bug. Absorbing one turns a step that never worked into a step
  that never reported, which is the failure mode the whole record exists to prevent.
* **`KeyboardInterrupt`** is the operator. A boundary that swallowed it would make Ctrl-C
  a thing that has to be pressed twice for reasons nobody can see.

**The type name only, never the message.** A driver's message carries the connection string
and an agent's carries whatever it was told; this line is written to a file the app renders
and an operator pastes into a chat. `lineup_refresh` learned that first and this is the one
copy now — a second boundary is a second chance to get the exception list wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def contained(step: Callable[[], T]) -> tuple[T | None, str]:
    """`(value, "")` or `(None, "TypeName")`. Never raises but for the two above."""
    try:
        return step(), ""
    except (AssertionError, KeyboardInterrupt):
        raise
    except BaseException as exc:
        return None, type(exc).__name__
