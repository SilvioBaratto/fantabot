/**
 * `fantabot db dump`, over the wire — a path, never the bytes.
 *
 * `tasks/archive/parity-spec.md` §8 Never #4 forbids the app from offering a browser download of a dump: the
 * file carries the `league_tokens` rows, encrypted but still credentials, and a download
 * puts it wherever the browser puts downloads — a directory covered by neither the
 * `/Volumes/` refusal nor `.gitignore`'s `*.dump`. Both guards are about where the file
 * is, so there is nothing in these two shapes that could carry it.
 */

/** Where today's dump would land — or, when `refused` is set, why nowhere would do. */
export interface DumpTarget {
  /** Empty exactly when `refused` is not. */
  path: string;
  /** The command's own sentence. The operator here and the one in a terminal read the same words. */
  refused: string;
  /** Whether today's dump has already been taken. A second run overwrites it. */
  exists: boolean;
  /** `null` when there is no file. A `0` would be a claim about an empty dump that exists. */
  size_bytes: number | null;
}

/** The started job, and the path it will write. */
export interface DumpStarted {
  outcome: string;
  path: string;
  job_id: string;
  detail: string;
}
