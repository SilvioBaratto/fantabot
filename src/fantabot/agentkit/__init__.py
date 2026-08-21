"""Claude Agent SDK plumbing shared by every fantabot command that queries.

Two callers need the same options builder, env guard and message loop:
``news-fetch`` (weekly, 523 players) and ``mantra-grid`` (one-off, two rules
pages). One caller would not justify a separate package; two do — and the
sibling ``optimizer-theory`` repo is explicit about what the alternative costs,
its own adapter opening with "The one message loop. Replaces five copies of it."

Nothing here knows about footballers. Nothing outside here imports
``claude_agent_sdk``.
"""
