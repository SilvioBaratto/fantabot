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
}

export interface JobList {
  jobs: JobSummary[];
}
