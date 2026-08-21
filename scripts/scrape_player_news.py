"""Scrape fantacalcio.it news-article results for every 2026/27 quotato player.

Source: https://www.fantacalcio.it/ricerca?q={surname}[&page=N] — a real,
working per-query search (confirmed by diffing results across several
players) that returns two sections: a "Calciatori" player card and an
"Articoli" list of dated news/calciomercato article cards. Each result is a
plain server-rendered <article class="article-card ..."> block — no XHR/API
call involved, verified via network capture — but it does require an
Accept-Language + Referer header or the server serves an empty "no results"
shell instead of the real content.

Player list comes from data/quotazioni_classic.csv (already scraped),
filtered to stagione == "2026/27" (this script is intentionally scoped to
the current season's quotato pool only, not the historical seasons that
file also contains). Search query uses the bare surname — the CSV's "nome"
field sometimes carries a disambiguating initial (e.g. "Kristensen T.",
"Martinez L.") that measurably hurts match quality, so it's stripped before
searching.

Usage:
    python scripts/scrape_player_news.py [--quotazioni data/quotazioni_classic.csv] [--out-dir data]

Writes:
    <out-dir>/player_news_2026-27.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://www.fantacalcio.it"
SEARCH_PATH = "/ricerca"
SEASON = "2026/27"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Referer": f"{BASE_URL}/",
}
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3
MAX_PAGES_PER_PLAYER = 20  # safety backstop; real loop stops at the first empty page

SUFFIX_INITIAL_RE = re.compile(r"\s+[A-Z]\.\s*$")
URL_DATE_RE = re.compile(r"/(\d{2})_(\d{2})_(\d{4})/")
# only keep articles from July/August of the season's start year (2026)
KEEP_YEAR = "2026"
KEEP_MONTHS = {"07", "08"}


@dataclass
class Player:
    player_id: str
    name: str
    team: str
    role: str

    @property
    def search_query(self) -> str:
        return SUFFIX_INITIAL_RE.sub("", self.name).strip()


@dataclass
class NewsItem:
    title: str = ""
    subtitle: str = ""
    category: str = ""
    display_time: str = ""
    url: str = ""
    content: str = ""

    @property
    def iso_date(self) -> str:
        m = URL_DATE_RE.search(self.url)
        if not m:
            return ""
        dd, mm, yyyy = m.groups()
        return f"{yyyy}-{mm}-{dd}"

    @property
    def time_of_day(self) -> str:
        # displayed as "16 lug - 14:37"; the URL already gives us the date part
        return self.display_time.rsplit("-", 1)[-1].strip()


class SearchResultsParser(HTMLParser):
    """Extracts one NewsItem per <article class="article-card ..."> block."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[NewsItem] = []
        self._item: NewsItem | None = None
        self._capture_key: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = (d.get("class") or "").split()

        if tag == "article" and "article-card" in classes:
            self._item = NewsItem()
            return

        if self._item is None:
            return

        if tag == "a" and "inner" in classes:
            href = d.get("href") or ""
            self._item.url = urllib.parse.urljoin(BASE_URL, href)
            return

        if tag == "h5" and "title" in classes:
            self._capture_key = "title"
        elif tag == "p" and "subtitle" in classes:
            self._capture_key = "subtitle"
        elif tag == "div" and classes == ["date"]:
            self._capture_key = "display_time"
        elif tag == "div" and classes == ["category"]:
            self._capture_key = "category"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h5", "p", "div"):
            self._capture_key = None
        elif tag == "article" and self._item is not None:
            if self._item.url:
                self.items.append(self._item)
            self._item = None

    def handle_data(self, data: str) -> None:
        if self._item is None or self._capture_key is None:
            return
        text = data.strip()
        if text:
            existing = getattr(self._item, self._capture_key)
            setattr(self._item, self._capture_key, f"{existing} {text}".strip())


class ArticleBodyParser(HTMLParser):
    """Extracts the readable text of one article page.

    The body lives in ``<div class="article-body">``; within it only
    ``<section class="article-content article-content-type-text">`` blocks hold
    prose (their siblings ``article-content-type-aa-*`` are ad slots). Inside a
    text section the prose is in ``<div class="text-type-default">`` as ``<p>``,
    ``<h2>``/``<h3>`` headings. We collect text from those tags and join blocks
    with a blank line, skipping ``&nbsp;``-only filler paragraphs.
    """

    _CAPTURE_TAGS = {"p", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__()
        self.text: str = ""
        self._in_body = False
        self._in_text_section = False
        self._depth_in_text_section = 0
        self._capture_tag: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        classes = (d.get("class") or "").split()

        if tag == "div" and "article-body" in classes:
            self._in_body = True
            return

        if not self._in_body:
            return

        if tag == "section" and "article-content" in classes:
            if "article-content-type-text" in classes:
                self._in_text_section = True
                self._depth_in_text_section = 1
            return

        if self._in_text_section and tag in self._CAPTURE_TAGS:
            self._capture_tag = tag
            self._buffer = []
        elif self._in_text_section:
            # nested element inside a capture tag — keep counting depth so the
            # matching endtag doesn't prematurely close the capture
            pass

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._in_body and not self._in_text_section:
            # closing the article-body div
            self._in_body = False
            return

        if tag == "section" and self._in_text_section:
            self._in_text_section = False
            self._depth_in_text_section = 0
            return

        if tag == self._capture_tag and self._capture_tag is not None:
            chunk = "".join(self._buffer)
            # normalize nbsp and collapse whitespace
            chunk = chunk.replace("\xa0", " ").strip()
            if chunk and chunk != "\xa0":
                self.text = f"{self.text}\n\n{chunk}".strip() if self.text else chunk
            self._capture_tag = None
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture_tag is not None:
            self._buffer.append(data)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body: bytes = resp.read()
            return body.decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
    assert last_error is not None
    raise last_error


def search_url(query: str, page: int) -> str:
    params = {"q": query}
    if page > 1:
        params["page"] = str(page)
    return f"{BASE_URL}{SEARCH_PATH}?{urllib.parse.urlencode(params)}"


def fetch_player_news(query: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    for page in range(1, MAX_PAGES_PER_PLAYER + 1):
        if page > 1:
            time.sleep(REQUEST_DELAY_SECONDS)
        html = fetch_html(search_url(query, page))
        parser = SearchResultsParser()
        parser.feed(html)
        if not parser.items:
            break
        items.extend(
            item
            for item in parser.items
            if item.category != "Mondiali"
            and item.iso_date
            and item.iso_date.startswith(KEEP_YEAR)
            and item.iso_date[5:7] in KEEP_MONTHS
        )
    return items


def fetch_article_content(url: str) -> str:
    """Fetch one article URL and return its cleaned body text (empty on failure)."""
    try:
        html = fetch_html(url)
    except Exception:
        return ""
    parser = ArticleBodyParser()
    parser.feed(html)
    return parser.text


def load_players_2026_27(quotazioni_path: Path) -> list[Player]:
    players: dict[str, Player] = {}
    with quotazioni_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["stagione"] != SEASON:
                continue
            players[row["id"]] = Player(
                player_id=row["id"],
                name=row["nome"],
                team=row["squadra"],
                role=row["ruolo"],
            )
    return sorted(players.values(), key=lambda p: (p.team, p.name))


def write_news_csv(rows: list[tuple[Player, NewsItem]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "nome",
                "squadra",
                "ruolo",
                "stagione",
                "data",
                "ora",
                "categoria",
                "titolo",
                "sottotitolo",
                "contenuto",
                "url",
            ]
        )
        for player, item in rows:
            writer.writerow(
                [
                    player.player_id,
                    player.name,
                    player.team,
                    player.role,
                    SEASON,
                    item.iso_date,
                    item.time_of_day,
                    item.category,
                    item.title,
                    item.subtitle,
                    item.content,
                    item.url,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quotazioni",
        type=Path,
        default=Path("data/quotazioni_classic.csv"),
        help="Path to the scraped quotazioni CSV to source the 2026/27 player list from",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("data"), help="Directory to write the CSV into"
    )
    args = parser.parse_args()

    players = load_players_2026_27(args.quotazioni)
    if not players:
        print(f"No {SEASON} players found in {args.quotazioni}.", file=sys.stderr)
        raise SystemExit(1)
    print(f"{len(players)} players in {SEASON} quotazioni")

    rows: list[tuple[Player, NewsItem]] = []
    for i, player in enumerate(players):
        if i > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        items = fetch_player_news(player.search_query)
        print(f"  {player.name} ({player.team}): {len(items)} articles")
        for item in items:
            time.sleep(REQUEST_DELAY_SECONDS)
            item.content = fetch_article_content(item.url)
            print(f"    -> {item.iso_date} {item.title[:50]!r}: {len(item.content)} chars")
        rows.extend((player, item) for item in items)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / "player_news_2026-27.csv"
    write_news_csv(rows, out_path)

    print(f"{len(rows)} articles across {len(players)} players -> {out_path}")


if __name__ == "__main__":
    main()
