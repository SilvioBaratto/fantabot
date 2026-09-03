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

import { PricingService } from '../../core/api/pricing.service';
import { TargetPricesReport } from '../../core/models/target-prices';

type System = 'classic' | 'mantra';

@Component({
  selector: 'app-prices',
  imports: [LucideAngularModule],
  templateUrl: './prices.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class PricesComponent implements OnInit {
  private readonly service = inject(PricingService);
  private readonly destroyRef = inject(DestroyRef);

  readonly system = signal<System>('classic');
  readonly report = signal<TargetPricesReport | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  setSystem(system: System): void {
    if (this.system() === system) return;
    this.system.set(system);
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getReport(this.system())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.report.set(report);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  flagList(): { flag: string; count: number }[] {
    const counts = this.report()?.flag_counts ?? {};
    return Object.entries(counts).map(([flag, count]) => ({ flag, count }));
  }
}
