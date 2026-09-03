export interface JobStatus {
  id: string;
  status: string; // running | done | error
  lines: string[];
  ok: boolean | null;
  error: string | null;
}
