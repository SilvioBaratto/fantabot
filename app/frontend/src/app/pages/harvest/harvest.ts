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
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, catchError, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { HarvestService } from '../../core/api/harvest.service';
import { JobsService } from '../../core/api/jobs.service';
import { Corpus, SeedPanel } from '../../core/models/corpus';

/** The job kind this page owns. `GET /jobs` lists every kind; only this one belongs here. */
const SCAN_KIND = 'harvest-scan';
/** The supervised child. Not a thread: `processes.py` carries the argument. */
const LOAD_KIND = 'harvest-load';

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
    // Its own read, and its own failure: a seed that cannot be counted says nothing
    // about the corpus, and folding the two would make one panel hide the other.
    this.service
      .getSeed()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((seed) => this.seed.set(seed));
  }

  /**
   * Ask FantaLab which auctions are live and merge them into the seed.
   *
   * There is no format argument here and none on the page: filtering is a query, never a
   * decision taken at collection time.
   */
  runScan(): void {
    if (this.scanning()) return;
    this.scanError.set(null);
    this.scanLines.set([]);
    this.scanOk.set(null);
    this.scanStatus.set('running');
    this.actions
      .runHarvestScan()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.scanning.set(true);
          this.poll(result.job_id);
        },
        error: () => {
          this.scanStatus.set('');
          this.scanError.set('Could not start the scan.');
        },
      });
  }

  /**
   * Carry the landing zone into Postgres, supervised as a child process.
   *
   * The format is a parameter of this read — a seed holds both and a load carries one —
   * and not the collection-time filter the scan is forbidden.
   */
  runLoad(): void {
    if (this.loaderRunning()) return;
    this.loaderError.set(null);
    this.loaderLines.set([]);
    this.loaderOk.set(null);
    this.loaderStatus.set('running');
    this.service
      .startLoad(this.loadFormat(), this.loadFollow())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.loaderRunning.set(true);
          this.loaderJobId.set(result.job_id);
          this.pollLoad(result.job_id);
        },
        error: (response: { error?: { detail?: string } }) => {
          this.loaderStatus.set('');
          this.loaderError.set(response?.error?.detail ?? 'Could not start the loader.');
        },
      });
  }

  /**
   * Ask the server to stop the child.
   *
   * A request and never a local forget: the child is a subprocess and outlives this page,
   * so dropping the id here would leave it running with nothing that knows how to stop it
   * but the role lock. The stop sequence itself — SIGINT, the lock, then SIGKILL — is the
   * server's, and its escalation shows up in this log.
   */
  stopLoad(): void {
    const jobId = this.loaderJobId();
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
   * One listing, both kinds: the server's list is the source of truth, so a refresh
   * mid-run reattaches rather than re-enabling a button that would start a second. The
   * child matters more than the scan does — it is a subprocess and survives the app
   * itself, so the only thing that could stop it otherwise is the role lock.
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
        const running = (kind: string) =>
          list.jobs.find((job) => job.kind === kind && job.status === 'running');

        const scan = running(SCAN_KIND);
        if (scan) {
          this.scanning.set(true);
          this.scanStatus.set('running');
          this.poll(scan.id);
        }

        const load = running(LOAD_KIND);
        if (load) {
          this.loaderRunning.set(true);
          this.loaderStatus.set('running');
          this.loaderJobId.set(load.id);
          this.pollLoad(load.id);
        }
      });
  }

  private pollLoad(jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, this.loaderLines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) this.loaderLines.update((shown) => [...shown, ...job.lines]);
        this.loaderStatus.set(job.status);
        if (job.status !== 'running') {
          this.loaderRunning.set(false);
          this.loaderJobId.set(null);
          this.loaderOk.set(job.ok);
          if (job.error) this.loaderError.set(job.error);
          // The corpus is what a load moves, so this is the one place re-reading it says
          // something true.
          this.load();
        }
      });
  }

  /** Poll one job, asking only for the lines it has not already shown. */
  private poll(jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, this.scanLines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) this.scanLines.update((shown) => [...shown, ...job.lines]);
        this.scanStatus.set(job.status);
        if (job.status !== 'running') {
          this.scanning.set(false);
          this.scanOk.set(job.ok);
          if (job.error) this.scanError.set(job.error);
          // The seed is what the scan just rewrote; the corpus is not, and re-reading it
          // here would suggest a scan collected something.
          this.service
            .getSeed()
            .pipe(
              catchError(() => EMPTY),
              takeUntilDestroyed(this.destroyRef),
            )
            .subscribe((seed) => this.seed.set(seed));
        }
      });
  }
}
