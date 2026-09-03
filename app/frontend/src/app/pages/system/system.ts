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

import { DbHealthService } from '../../core/api/db-health.service';
import { DbHealth } from '../../core/models/db-health';

@Component({
  selector: 'app-system',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './system.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class SystemComponent implements OnInit {
  private readonly service = inject(DbHealthService);
  private readonly destroyRef = inject(DestroyRef);

  readonly health = signal<DbHealth | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getHealth()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (health) => {
          this.health.set(health);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }
}
