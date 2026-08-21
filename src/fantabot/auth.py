from rich.console import Console

from fantabot import browser
from fantabot.config import settings

console = Console()


def run() -> None:
    """Open a real Chrome window, let the human log in + solve any captcha/2FA,
    then persist cookies/localStorage to storage_state.json so every other
    command runs headless from here on.

    Deliberately manual, not scripted: leghe.fantacalcio.it's login form isn't
    inspected yet, and blind-typing credentials into unverified selectors is
    how you get a bot flagged or a wrong click. Revisit once the login DOM is
    confirmed, if fully unattended re-auth turns out to be needed.
    """
    url = settings.lega_url or "https://leghe.fantacalcio.it"
    console.print(f"[bold]Opening {url} — log in manually, then press Enter here.[/bold]")
    with browser.interactive_login_context() as ctx:
        page = ctx.new_page()
        page.goto(url)
        input("Press Enter once you're logged in and see your league home page... ")
    console.print(f"[green]Session saved to {settings.fantabot_storage_state}[/green]")
