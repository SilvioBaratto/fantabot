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
   * Pick up a scan still running from an earlier page load.
   *
   * The server's listing is the source of truth, so a refresh mid-scan reattaches rather
   * than re-enabling a button that would start a second one.
   */
  private reattach(): void {
    this.jobs
      .list()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((list) => {
        const live = list.jobs.find((job) => job.kind === SCAN_KIND && job.status === 'running');
        if (!live) return;
        this.scanning.set(true);
        this.scanStatus.set('running');
        this.poll(live.id);
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
