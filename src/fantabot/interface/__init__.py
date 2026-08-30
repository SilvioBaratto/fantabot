"""The CLI layer: Typer commands, and the objects every command shares.

Small on purpose. Two modules live here today — the one `Console` and the option
groups declared more than once — and both exist because the alternative was a
circular import.
"""
