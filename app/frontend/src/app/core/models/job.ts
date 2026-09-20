export interface JobStatus {
  id: string;
  status: string; // running | done | error
  lines: string[];
  /** Where to resume from: send it back as `?since=` so a poll costs only what is new. */
  next_index?: number;
  ok: boolean | null;
  error: string | null;
  /** True while the job is parked waiting for you to confirm. */
  awaiting_confirm?: boolean;
}

/** One row of `GET /jobs` — no lines, by design: the list is polled, the log is not. */
export interface JobSummary {
  id: string;
  kind: string;
  status: string;
  started_at: string;
  line_count: number;
  ok: boolean | null;
  stoppable: boolean;
  /**
   * Whether this job's child can act. `null` where the question does not arise — every kind
   * but the bidder.
   *
   * A reloaded tab finds its run through `GET /jobs` and has no other route back to the
   * arming decision the request made. Without this it drew a live armed bidder exactly as it
   * draws a rehearsal, at the one moment the distinction is worth anything.
   */
  armed?: boolean | null;
}

export interface JobList {
  jobs: JobSummary[];
}
