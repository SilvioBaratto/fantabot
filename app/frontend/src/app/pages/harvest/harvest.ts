import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import type { WritableSignal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, Observable, catchError, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { HarvestService } from '../../core/api/harvest.service';
import { JobsService } from '../../core/api/jobs.service';
import { Corpus, SeedPanel } from '../../core/models/corpus';

/** The job kind this page owns. `GET /jobs` lists every kind; only this one belongs here. */
const SCAN_KIND = 'harvest-scan';
/** The supervised child. Not a thread: `processes.py` carries the argument. */
const LOAD_KIND = 'harvest-load';
/** The other supervised child, and the irreplaceable one. */
const COLLECT_KIND = 'harvest-collect';

/**
 * The four signals every job on this page renders through.
 *
 * Written down at the third one, not the first: scan, load and collect poll the same
 * endpoint and differ only in what they re-read when it finishes. Three copies of the
 * poll had already drifted once by then in the pages this one was modelled on.
 */
interface JobPanel {
  readonly running: WritableSignal<boolean>;
  readonly lines: WritableSignal<string[]>;
  readonly status: WritableSignal<string>;
  readonly ok: WritableSignal<boolean | null>;
  readonly error: WritableSignal<string | null>;
  readonly jobId: WritableSignal<string | null>;
}

/**
 * What the harvest actually put in the database, per format — and what the next collect
 * would follow.
 *
 * The corpus panel was built before anything with a lifecycle, and `todo/TODO.md` §2 says
 * why: it is the instrument every later increment is graded on, and without it
 * "collection worked" is unfalsifiable. §1.1 is the case in point — the Classic corpus
 * read as 2.1 million events and zero sales for over a week, and no screen could tell
 * "nothing was collected" from "everything was collected and nothing joined".
 *
 * The seed panel sits beside it and is never summed with it: a scan grows the registry
 * whether or not a single frame was ever received.
 */
@Component({
  selector: 'app-harvest',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './harvest.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class HarvestComponent implements OnInit {
  private readonly service = inject(HarvestService);
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly corpus = signal<Corpus | null>(null);
  readonly seed = signal<SeedPanel | null>(null);
  readonly loading = signal(true);
  /** The API could not be reached at all — distinct from a corpus that read `ok: false`. */
  readonly errorMsg = signal<string | null>(null);

  readonly scanning = signal(false);
  readonly scanLines = signal<string[]>([]);
  readonly scanStatus = signal<string>('');
  readonly scanOk = signal<boolean | null>(null);
  readonly scanError = signal<string | null>(null);

  readonly collecting = signal(false);
  readonly collectLines = signal<string[]>([]);
  readonly collectStatus = signal<string>('');
  readonly collectOk = signal<boolean | null>(null);
  readonly collectError = signal<string | null>(null);
  readonly collectJobId = signal<string | null>(null);
  /**
   * Concurrent streams. Pre-filled above the population and never at a default below it:
   * a pool below the population is silent starvation — a watcher on a live evening does
   * not finish, so a queued auction never gets a permit and never connects at all.
   */
  readonly pool = signal(0);

  readonly loadFormat = signal('mantra');
  readonly loadFollow = signal(true);
  readonly loaderRunning = signal(false);
  readonly loaderLines = signal<string[]>([]);
  readonly loaderStatus = signal<string>('');
  readonly loaderOk = signal<boolean | null>(null);
  readonly loaderError = signal<string | null>(null);
  /** The server's id for the running child. Stopping is a request, never a local forget. */
  readonly loaderJobId = signal<string | null>(null);

  readonly formats = ['mantra', 'classic'] as const;

  private readonly scanPanel: JobPanel = {
    running: this.scanning,
    lines: this.scanLines,
    status: this.scanStatus,
    ok: this.scanOk,
    error: this.scanError,
    jobId: signal<string | null>(null),
  };

  private readonly loadPanel: JobPanel = {
    running: this.loaderRunning,
    lines: this.loaderLines,
    status: this.loaderStatus,
    ok: this.loaderOk,
    error: this.loaderError,
    jobId: this.loaderJobId,
  };

  private readonly collectPanel: JobPanel = {
    running: this.collecting,
    lines: this.collectLines,
    status: this.collectStatus,
    ok: this.collectOk,
    error: this.collectError,
    jobId: this.collectJobId,
  };

  /** `{classic: 1226}` as `[['classic', 1226]]`, because a template cannot iterate a record. */
  readonly seedFormats = computed(() =>
    Object.entries(this.seed()?.formats ?? {}).sort(([a], [b]) => a.localeCompare(b)),
  );

  ngOnInit(): void {
    this.load();
    this.reattach();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getCorpus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (corpus) => {
          this.corpus.set(corpus);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
    this.readSeed();
  }

  /**
   * Ask FantaLab which auctions are live and merge them into the seed.
   *
   * There is no format argument here and none on the page: filtering is a query, never a
   * decision taken at collection time.
   *
   * When it finishes it re-reads the **seed and only the seed**. Re-reading the corpus
   * would suggest a scan collected something; a merge adds and updates and never removes.
   */
  runScan(): void {
    this.start(this.scanPanel, this.actions.runHarvestScan(), 'Could not start the scan.', () =>
      this.readSeed(),
    );
  }

  /**
   * Carry the landing zone into Postgres, supervised as a child process.
   *
   * The format is a parameter of this read — a seed holds both and a load carries one —
   * and not the collection-time filter the scan is forbidden. The corpus is what a load
   * moves, so this is the one place where re-reading it says something true.
   */
  runLoad(): void {
    this.start(
      this.loadPanel,
      this.service.startLoad(this.loadFormat(), this.loadFollow()),
      'Could not start the loader.',
      () => this.load(),
    );
  }

  /**
   * Subscribe to the live auctions in the seed and append every state to the landing zone.
   *
   * The pool is sent as it is shown. The server refuses a pool below the population and
   * says both numbers; that refusal is rendered verbatim, because replacing it with a
   * generic message is how the one fact the operator needs stops reaching them.
   */
  runCollect(): void {
    this.start(
      this.collectPanel,
      this.service.startCollect(this.pool()),
      'Could not start the collector.',
      () => this.load(),
    );
  }

  /** Ask the server to stop the supervised loader. */
  stopLoad(): void {
    this.stopJob(this.loadPanel);
  }

  /** Ask the server to stop the supervised collector. */
  stopCollect(): void {
    this.stopJob(this.collectPanel);
  }

  // -- the shared machinery ------------------------------------------------------------

  private readSeed(): void {
    // Its own read and its own failure: a seed that cannot be counted says nothing about
    // the corpus, and folding the two would let one panel hide the other.
    this.service
      .getSeed()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((seed) => {
        this.seed.set(seed);
        // Above the population, never at a default below it. The default is the server's,
        // so this does not hardcode a constant that has already moved once.
        this.pool.set(Math.max(seed.default_pool, seed.rows));
      });
  }

  /**
   * Start one job into one panel.
   *
   * The failure text is the server's `detail` when there is one: a refusal that carries
   * both halves of "pool is 1000 and the seed holds 1705" is the whole value of that
   * refusal, and a generic fallback would throw it away.
   */
  private start(
    panel: JobPanel,
    request: Observable<{ job_id: string }>,
    fallback: string,
    onFinish: () => void,
  ): void {
    if (panel.running()) return;
    panel.error.set(null);
    panel.lines.set([]);
    panel.ok.set(null);
    panel.status.set('running');
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => {
        panel.running.set(true);
        panel.jobId.set(result.job_id);
        this.track(panel, result.job_id, onFinish);
      },
      error: (response: { error?: { detail?: string } }) => {
        panel.status.set('');
        panel.error.set(response?.error?.detail ?? fallback);
      },
    });
  }

  /**
   * A request to the server, never a local forget.
   *
   * The supervised children are subprocesses and outlive this page, so dropping the id
   * here would leave one running with nothing that knows how to stop it but the role
   * lock. The stop sequence itself — SIGINT, the lock, then SIGKILL — is the server's,
   * and its escalation shows up in this log.
   */
  private stopJob(panel: JobPanel): void {
    const jobId = panel.jobId();
    if (!jobId) return;
    this.jobs
      .stop(jobId)
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  /**
   * Pick up whatever this page owns that is still running from an earlier page load.
   *
   * One listing, all three kinds: the server's list is the source of truth, so a refresh
   * mid-run reattaches rather than re-enabling a button that would start a second. It
   * matters most for the two children — they are subprocesses and survive the app itself,
   * so the only thing that could stop one otherwise is the role lock.
   *
   * A listing that cannot be read is not an error worth showing: nothing the operator
   * asked for has failed, and a red banner on arrival would be about the poll, not them.
   */
  private reattach(): void {
    this.jobs
      .list()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((list) => {
        const resume = (kind: string, panel: JobPanel, onFinish: () => void) => {
          const live = list.jobs.find((job) => job.kind === kind && job.status === 'running');
          if (!live) return;
          panel.running.set(true);
          panel.status.set('running');
          panel.jobId.set(live.id);
          this.track(panel, live.id, onFinish);
        };

        resume(SCAN_KIND, this.scanPanel, () => this.readSeed());
        resume(LOAD_KIND, this.loadPanel, () => this.load());
        resume(COLLECT_KIND, this.collectPanel, () => this.load());
      });
  }

  /** Poll one job into one panel, asking only for the lines it has not already shown. */
  private track(panel: JobPanel, jobId: string, onFinish: () => void): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, panel.lines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) panel.lines.update((shown) => [...shown, ...job.lines]);
        panel.status.set(job.status);
        if (job.status === 'running') return;
        panel.running.set(false);
        panel.jobId.set(null);
        panel.ok.set(job.ok);
        if (job.error) panel.error.set(job.error);
        onFinish();
      });
  }
}
