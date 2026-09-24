"""Claude Agent SDK plumbing shared by every fantabot command that queries.

Three callers need the same options builder and message loop:
``application/news_fetcher`` (``news fetch``, weekly, 523 players),
``application/mantra_collector`` (``mantra-grid``, one-off, two rules pages) and
``application/asta_copilot`` (the live room's LLM pane). One caller would not justify
a separate package; two did when this said "two", and three make the argument
a fortiori. The **env guard** is a separate list: ``strip_dangerous_env`` is called from
``interface/app.py`` (twice) and ``application/news_roster``, none of the three above — it
runs where the command is assembled, not where the loop is. Saying "these three need the
guard" was true of the *commands* and is false of the *modules*, which is the distinction
this sentence lost when it was re-pointed at modules.
The sibling ``optimizer-theory`` repo is explicit about what the
alternative costs, its own adapter opening with "The one message loop. Replaces
five copies of it."

Nothing here knows about footballers. Nothing outside here imports
``claude_agent_sdk``.
"""
