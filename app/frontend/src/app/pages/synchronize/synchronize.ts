import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, Observable, catchError, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { JobsService } from '../../core/api/jobs.service';

/** The job kind this page owns. `GET /jobs` lists every kind; only one belongs here. */
const KIND = 'lega-sync';

@Component({
  selector: 'app-synchronize',
  imports: [LucideAngularModule],
  templateUrl: './synchronize.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class SynchronizeComponent {
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly leagueId = signal<number | null>(null);
  readonly running = signal(false);
  readonly lines = signal<string[]>([]);
  readonly jobStatus = signal<string>('');
  readonly jobOk = signal<boolean | null>(null);
  readonly errorMsg = signal<string | null>(null);

  constructor() {
    this.reattach();
  }

  setLeagueId(value: string): void {
    const parsed = Number(value);
    this.leagueId.set(Number.isFinite(parsed) && value.trim() !== '' ? parsed : null);
  }

  runLegaSync(): void {
    const id = this.leagueId();
    if (!id || this.running()) return;
    this.startJob(this.actions.runLegaSync(id));
  }

  /**
   * Pick up a sync that is still running from an earlier page load.
   *
   * The running job id used to live only here, so a refresh mid-sync orphaned the job
   * invisibly *and* re-enabled the button that would start a second one. The server's
   * listing is the source of truth now.
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

  private startJob(request: Observable<{ job_id: string }>): void {
    this.errorMsg.set(null);
    this.lines.set([]);
    this.jobOk.set(null);
    this.jobStatus.set('running');
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => {
        this.running.set(true);
        this.poll(result.job_id);
      },
      error: () => {
        this.jobStatus.set('');
        this.errorMsg.set('Could not start the job.');
      },
    });
  }

  /**
   * Poll one job, asking only for the lines it has not already shown.
   *
   * `since` starts wherever `lines` already is, so a reattach after a refresh does not
   * re-render the whole log, and a resumed poll appends rather than replaces.
   */
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
        if (job.status !== 'running') {
          this.running.set(false);
          this.jobOk.set(job.ok);
        }
      });
  }
}
