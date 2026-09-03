import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { Observable, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { JobsService } from '../../core/api/jobs.service';

@Component({
  selector: 'app-actions',
  imports: [LucideAngularModule],
  templateUrl: './actions.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class ActionsComponent {
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly leagueId = signal<number | null>(null);
  readonly season = signal('2026/27');
  readonly running = signal(false);
  readonly lines = signal<string[]>([]);
  readonly jobStatus = signal<string>('');
  readonly jobOk = signal<boolean | null>(null);
  readonly errorMsg = signal<string | null>(null);

  setLeagueId(value: string): void {
    const parsed = Number(value);
    this.leagueId.set(Number.isFinite(parsed) && value.trim() !== '' ? parsed : null);
  }

  setSeason(value: string): void {
    this.season.set(value);
  }

  runLegaSync(): void {
    const id = this.leagueId();
    if (!id || this.running()) return;
    this.startJob(this.actions.runLegaSync(id));
  }

  runNewsFetch(): void {
    if (this.running() || !this.season().trim()) return;
    this.startJob(this.actions.runNewsFetch(this.season()));
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

  private poll(jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        this.lines.set(job.lines);
        this.jobStatus.set(job.status);
        if (job.status !== 'running') {
          this.running.set(false);
          this.jobOk.set(job.ok);
        }
      });
  }
}
