export interface JobStatus {
  id: string;
  status: string; // running | done | error
  lines: string[];
  ok: boolean | null;
  error: string | null;
  /** True while the job is parked waiting for you to confirm. */
  awaiting_confirm?: boolean;
}
