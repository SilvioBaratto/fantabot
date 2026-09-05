import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, catchError, forkJoin, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { JobsService } from '../../core/api/jobs.service';
import { NewsService } from '../../core/api/news.service';
import { DriftRow, NewsRow } from '../../core/models/news';

/**
 * The job kind this page owns. `GET /jobs` lists every kind; only this one belongs
 * here — the fetch lives on the News page and nowhere else (§7 of the archived phase
 * spec, `tasks/archive/fantalab-in-the-app-spec.md`).
 */
const KIND = 'news-fetch';

@Component({
  selector: 'app-news',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './news.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class NewsComponent implements OnInit {
  private readonly service = inject(NewsService);
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly feed = signal<NewsRow[]>([]);
  readonly drifted = signal<DriftRow[]>([]);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);
  readonly loaded = signal(false);

  /**
   * The fetch. Its home, since it was taken off Synchronize at the operator's request
   * and the endpoint deliberately kept: this is the topic's own page, so it adds no nav
   * entry to a mobile bar that already carries nine tabs (`todo/TODO.md` §3.8).
   */
  readonly season = signal('2026/27');
  readonly running = signal(false);
  readonly lines = signal<string[]>([]);
  readonly jobStatus = signal<string>('');
  readonly jobOk = signal<boolean | null>(null);
  readonly jobError = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
    this.reattach();
  }

  setSeason(value: string): void {
    this.season.set(value);
  }

  /**
   * Start the fetch and follow it.
   *
   * It is 523 players through the Agent SDK and it costs tokens, so a second one started
   * by accident is not free — which is why the button is disabled while one runs and why
   * `reattach` exists rather than the id living only in this page.
   */
  runFetch(): void {
    if (this.running()) return;
    this.jobError.set(null);
    this.lines.set([]);
    this.jobOk.set(null);
    this.jobStatus.set('running');
    this.actions
      .runNewsFetch(this.season())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.running.set(true);
          this.poll(result.job_id);
        },
        error: (response: { error?: { detail?: string } }) => {
          this.jobStatus.set('');
          // The server's own words when it has any: a missing key and a dead agent
          // model are different problems and only one of them is fixed in `.env`.
          this.jobError.set(response?.error?.detail ?? 'Could not start the fetch.');
        },
      });
  }

  /**
   * Pick up a fetch still running from an earlier page load.
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
        const live = list.jobs.find((job) => job.kind === KIND && job.status === 'running');
        if (!live) return;
        this.running.set(true);
        this.jobStatus.set('running');
        this.poll(live.id);
      });
  }

  /** Poll one job, asking only for the lines it has not already shown. */
  private poll(jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, this.lines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) this.lines.update((shown) => [...shown, ...job.lines]);
        this.jobStatus.set(job.status);
        if (job.status === 'running') return;
        this.running.set(false);
        this.jobOk.set(job.ok);
        if (job.error) this.jobError.set(job.error);
        // The fetch is what puts rows on this screen, so re-reading here is the one
        // place where it says something true rather than looking busy.
        this.load();
      });
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    forkJoin({ feed: this.service.getFeed(50), drifted: this.service.getDrifted() })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ feed, drifted }) => {
          this.feed.set(feed);
          this.drifted.set(drifted);
          this.loaded.set(true);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  sentimentTone(value: number): 'up' | 'down' | 'flat' {
    if (value > 0.05) return 'up';
    if (value < -0.05) return 'down';
    return 'flat';
  }
}
