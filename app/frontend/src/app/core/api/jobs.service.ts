import { HttpClient, HttpParams } from '@angular/common/http';
import { DestroyRef, Injectable, WritableSignal, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, Observable, catchError, interval, map, switchMap, takeWhile } from 'rxjs';

import { environment } from '../../../environments/environment';
import { JobList, JobStatus, JobSummary } from '../models/job';

/**
 * How often a running job is asked what it has done since.
 *
 * One number, because it was three. Not a preference: it is what `?since=` is worth —
 * a 1.5 s poll costs what happened since the last one, and the log a person reads has
 * to arrive at about the speed they read it.
 */
const POLL_MS = 1500;

/**
 * The signals one job's worth of page state is written into.
 *
 * `harvest` and `synchronize` each declared this record under this name; `news` held the
 * same five signals loose on the component. One declaration now, because the poll that
 * writes them is one function and a record it cannot rely on is a record it cannot write.
 *
 * **`jobId` and `error` are optional, and that is a statement about the pages, not a
 * convenience.** `track` writes neither: the two pages that have both disagree about what
 * the end of a job does to them, and picking a winner here would be a behaviour change
 * wearing a refactor's clothes. See `track`.
 */
export interface JobPanel {
  readonly running: WritableSignal<boolean>;
  readonly lines: WritableSignal<string[]>;
  readonly status: WritableSignal<string>;
  readonly ok: WritableSignal<boolean | null>;
  /** The handle a stop control addresses. Whoever sets it decides when to drop it. */
  readonly jobId?: WritableSignal<string | null>;
  /** The server's own words when a child died holding one. See `track`. */
  readonly error?: WritableSignal<string | null>;
}

@Injectable({ providedIn: 'root' })
export class JobsService {
  private readonly http = inject(HttpClient);

  /**
   * One job. With `since`, only the lines from that index onward — a 1.5 s poll then
   * costs what happened since the last one instead of the whole log every time.
   */
  get(jobId: string, since?: number): Observable<JobStatus> {
    const options = since === undefined ? {} : { params: new HttpParams().set('since', since) };
    return this.http.get<JobStatus>(`${environment.apiUrl}jobs/${jobId}`, options);
  }

  /** What the server is running. The source of truth a page reattaches from. */
  list(): Observable<JobList> {
    return this.http.get<JobList>(`${environment.apiUrl}jobs`);
  }

  /**
   * Every job the server is **running**, once, with the listing's own failure swallowed.
   *
   * Two decisions, each of which was written out five times.
   *
   * **Running, not merely present.** `GET /jobs` lists everything this process has ever
   * run, so a `done` row taken for a live one tails a room that closed hours ago and
   * calls it live — or, on the pages with a start button, leaves it disabled against a
   * job that ended.
   *
   * **A listing that cannot be read is not an error worth showing.** Nothing the operator
   * asked for has failed; a red banner on arrival would be about the poll rather than
   * about them. So the stream completes empty and the page stays as it was.
   *
   * The caller still picks its own kinds out of the result. That part legitimately
   * differs — `harvest` resumes four panels off one listing, `accounts` accepts either
   * login kind, `asta` tells a watch from a bid — and folding it in here would be one
   * function answering five questions.
   */
  running(destroyRef: DestroyRef): Observable<JobSummary[]> {
    return this.list().pipe(
      map((list) => list.jobs.filter((job) => job.status === 'running')),
      catchError(() => EMPTY),
      takeUntilDestroyed(destroyRef),
    );
  }

  /**
   * Follow one job into one panel until it ends, asking only for what it has not shown.
   *
   * This loop was written three times — `harvest`, `synchronize`, `news` — and the copies
   * had already drifted. `since` is `panel.lines().length` rather than a counter of its
   * own, so a reattach after a refresh appends where the log already is instead of
   * re-rendering it from the top; `takeWhile(..., true)` keeps the terminal frame, which
   * is the one carrying `ok` and `error`.
   *
   * **What it deliberately does not do.** It never writes `panel.jobId` and it writes
   * `panel.error` only when the panel has one. Those are exactly the two points the three
   * copies disagreed on, and each disagreement is a live difference an operator would
   * see:
   *
   * * `harvest` drops the id when a job ends and `synchronize` keeps it — so `harvest`'s
   *   stop control has nothing left to address and `synchronize`'s would send a stop to a
   *   finished job, were it reachable (it sits inside `@if (scrapeRunning())`).
   * * `harvest` and `news` render `job.error`; `synchronize`'s panel has no `error` signal
   *   at all, so a crashed scrape child shows the generic "The scrape did not finish" and
   *   the `"{ExcType}: {msg}"` the server took the trouble to carry is dropped. Giving
   *   `scrapePanel` an `error` is the whole fix and it is one line — and it is a change to
   *   what a person sees, so it is the repository's to make and not this collapse's.
   *
   * `onFinish` is the caller's: a load re-reads the corpus, a scan re-reads only the seed,
   * a fetch re-reads the feed it just wrote, and a sync re-reads nothing.
   */
  track(options: {
    jobId: string;
    panel: JobPanel;
    destroyRef: DestroyRef;
    onFinish?: () => void;
  }): void {
    const { jobId, panel, destroyRef, onFinish } = options;
    interval(POLL_MS)
      .pipe(
        switchMap(() => this.get(jobId, panel.lines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) panel.lines.update((shown) => [...shown, ...job.lines]);
        panel.status.set(job.status);
        if (job.status === 'running') return;
        panel.running.set(false);
        panel.ok.set(job.ok);
        if (job.error) panel.error?.set(job.error);
        onFinish?.();
      });
  }

  /** Ask a job to stop. 409 means it has no way to be stopped — not that it failed. */
  stop(jobId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(`${environment.apiUrl}jobs/${jobId}/stop`, {});
  }
}
