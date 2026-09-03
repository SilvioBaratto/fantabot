import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';
import { interval, switchMap, takeWhile } from 'rxjs';

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
  readonly running = signal(false);
  readonly lines = signal<string[]>([]);
  readonly jobStatus = signal<string>('');
  readonly jobOk = signal<boolean | null>(null);
  readonly errorMsg = signal<string | null>(null);

  setLeagueId(value: string): void {
    const parsed = Number(value);
    this.leagueId.set(Number.isFinite(parsed) && value.trim() !== '' ? parsed : null);
  }

  runLegaSync(): void {
    const id = this.leagueId();
    if (!id || this.running()) return;
    this.errorMsg.set(null);
    this.lines.set([]);
    this.jobOk.set(null);
    this.jobStatus.set('running');
    this.actions
      .runLegaSync(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.running.set(true);
          this.poll(result.job_id);
        },
        error: () => this.errorMsg.set('Could not start the sync.'),
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
