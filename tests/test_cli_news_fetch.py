"""news fetch behaviours that need neither the database nor an agent.

The rest moved to tests/integration/: the pool is a query now, so the command
needs the stack up even for --no-run.
"""

from pathlib import Path

import _importgraph as G
from typer.testing import CliRunner

from fantabot.cli import app

#: The persistence package, spelled once. Its name moves in W6.
PERSISTENCE = "fantabot.adapters.persistence"

runner = CliRunner()


def test_scope_roster_errors_instead_of_silently_fetching_the_whole_pool() -> None:
    # Falling back to `pool` would spend 523 queries for someone who asked for ~25,
    # and look like it worked.
    result = runner.invoke(app, ["news", "fetch", "--scope", "roster"])

    assert result.exit_code != 0
    output = result.output.lower()
    assert "roster" in output
    assert "api" in output  # names the league-API work as the blocker


def test_the_pipeline_never_writes() -> None:
    """CLAUDE.md's rule and nine tests depend on fetch_all returning a result
    rather than persisting one. Pushing inserts into it would make the fan-out
    untestable without a database.

    The module is located through the import system rather than by a path literal, so
    W6 changes its name here and nothing else. The database half is asked of the import
    graph; the other two stay text checks because they name *calls*, which an import
    graph cannot see, and because being blunt is the point -- P11-4 tried to move a read
    in here and this is what stopped it.
    """
    import fantabot.application.news_fetcher as pipeline

    source = Path(pipeline.__file__).read_text()

    assert not G.reaches(pipeline.__name__, PERSISTENCE)
    assert "upsert" not in source
    assert "append_rows" not in source
