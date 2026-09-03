import { DatePipe } from '@angular/common';
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
import { interval, switchMap, takeWhile } from 'rxjs';

import { AuthService } from '../../core/api/auth.service';
import { JobsService } from '../../core/api/jobs.service';
import { AuthStatus } from '../../core/models/auth-status';

type Tone = 'ok' | 'warn' | 'bad';

@Component({
  selector: 'app-accounts',
  imports: [LucideAngularModule, DatePipe],
  templateUrl: './accounts.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class AccountsComponent implements OnInit {
  private readonly service = inject(AuthService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly status = signal<AuthStatus | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  // Connect-account flow
  readonly connecting = signal(false);
  readonly finishing = signal(false);
  readonly connectError = signal<string | null>(null);
  private jobId: string | null = null;

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getStatus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (status) => {
          this.status.set(status);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  onConnect(): void {
    this.connectError.set(null);
    this.service
      .startLogin()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.jobId = result.job_id;
          this.connecting.set(true);
        },
        error: () => this.connectError.set('Could not start login.'),
      });
  }

  onContinue(): void {
    if (!this.jobId) return;
    this.finishing.set(true);
    this.service
      .confirmLogin(this.jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.pollUntilDone(),
        error: () => {
          this.finishing.set(false);
          this.connectError.set('Could not confirm login.');
        },
      });
  }

  cancelConnect(): void {
    this.connecting.set(false);
    this.finishing.set(false);
    this.jobId = null;
  }

  tone(state: string): Tone {
    if (state.startsWith('ok')) return 'ok';
    if (state.startsWith('ORPHANED')) return 'warn';
    return 'bad'; // EXPIRED, KEY MISMATCH, MISSING
  }

  private pollUntilDone(): void {
    const id = this.jobId;
    if (!id) return;
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(id)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.status !== 'running') {
          if (job.status === 'error') this.connectError.set('Login failed. Try again.');
          this.connecting.set(false);
          this.finishing.set(false);
          this.jobId = null;
          this.load();
        }
      });
  }
}
