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

import { AuthService } from '../../core/api/auth.service';
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
  private readonly destroyRef = inject(DestroyRef);

  readonly status = signal<AuthStatus | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

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

  tone(state: string): Tone {
    if (state.startsWith('ok')) return 'ok';
    if (state.startsWith('ORPHANED')) return 'warn';
    return 'bad'; // EXPIRED, KEY MISMATCH, MISSING
  }
}
