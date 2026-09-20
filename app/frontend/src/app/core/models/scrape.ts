/**
 * One table `db scrape` may be pointed at, as the form needs to see it.
 *
 * `default_seasons` is the scraper's **own** `DEFAULT_SEASONS`, read live on the server
 * rather than copied anywhere. It is on the wire because it is the trap: `voti` and
 * `statistiche` stop at 2025/26 while 2026/27 is being played, so a run that names no
 * season scrapes last season and reports success.
 */
export interface ScrapeTable {
  table: string;
  /** The rows it upserts, so the operator knows what a run touches before it runs. */
  writes: string[];
  /**
   * What must already have been scraped. `players` and `teams` have no outbound foreign
   * keys and everything else points at them, so on a fresh database anything but
   * `quotazioni` first is a violation rather than a slow run.
   */
  requires: string[];
  default_seasons: string[];
  /** Whether that list misses the season being played. Two of the three do. */
  default_is_stale: boolean;
}

/**
 * The picker's contents, and the season the staleness was measured against.
 *
 * `current_season` comes from the server rather than from a `new Date()` here. A browser
 * in another timezone would disagree with the server about which season is being played,
 * and the disagreement would surface as a form default nobody chose.
 */
export interface ScrapeTables {
  current_season: string;
  tables: ScrapeTable[];
}

/**
 * What `POST /db/scrape` takes.
 *
 * An empty `seasons` means the scraper's own default, which is what the command does.
 * The form never sends one — it defaults the field to the season being played — but the
 * route still accepts it, because §8 Never #4 gives the app no power the CLI lacks and
 * gives this route no restriction it has not got either.
 */
export interface ScrapeRequest {
  table: string;
  seasons: string[];
}
