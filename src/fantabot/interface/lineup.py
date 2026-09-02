"""`fantabot lineup` — read, plan and submit the weekly Mantra formazione.

Typer only, like the rest of `interface/`. The one network call goes through
`adapters/http/apileague`'s `gaming/v1` client; the value model, schema and matcher live in
`domain/lineup` and are reached through `application/lineup_planner` (later tasks). This
module holds the commands and the presentation; nothing here decides a lineup.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import typer

from fantabot.interface.console import console


def format_lineup(dto: Mapping[str, Any]) -> list[str]:
    """Render a `teamLineupDto` for the console. Pure — takes the parsed body, no I/O.

    Ids, not names: `show` is the raw read that proves the `gaming/v1` path; the
    name-resolved, value-annotated view is `plan`, once the roster is assembled.
    """
    if not dto:
        return ["no lineup set for this competition"]
    module = dto.get("mdl", "?")
    starts = list(dto.get("starts", []))
    bench = list(dto.get("bench", []))
    return [
        f"module {module}",
        f"starters ({len(starts)}): {' '.join(str(p) for p in starts)}",
        f"bench ({len(bench)}): {' '.join(str(p) for p in bench)}",
    ]


def _show(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    competition: int = typer.Option(0, "--competition", help="Competition id (required)."),
) -> None:
    """Print the current lineup for a competition.

    One network call, `apileague.teamLineup_read`
    (`GET /gaming/v1/teamLineup/visualizza/A/{competition}`), authenticated with the token
    already stored for `league`. Nothing here logs in or opens a browser; the bearer used to
    fetch is never printed, only the ids it returns.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.http import apileague
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = league or settings.fantabot_league_id
    if not league_id:
        console.print("[red]no lega id: pass --league or set FANTABOT_LEAGUE_ID[/red]")
        raise typer.Exit(code=1)
    if not competition:
        console.print("[red]no competition id: pass --competition[/red]")
        raise typer.Exit(code=1)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            body = apileague.teamLineup_read(league_id, competition, store=store)
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for line in format_lineup(body.get("teamLineupDto", {})):
        console.print(line)


def register(app: typer.Typer) -> None:
    """Attach the lineup commands to the `lineup` group (called from `interface/app`)."""
    app.command("show")(_show)
