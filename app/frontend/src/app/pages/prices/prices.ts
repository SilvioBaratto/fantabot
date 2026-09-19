import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButton } from '@angular/material/button';
import { MatButtonToggle, MatButtonToggleGroup } from '@angular/material/button-toggle';
import { MatCard } from '@angular/material/card';
import { MatChip, MatChipSet } from '@angular/material/chips';
import { MatProgressBar } from '@angular/material/progress-bar';
import { LucideAngularModule } from 'lucide-angular';

import { PricingService } from '../../core/api/pricing.service';
import { TargetPricesReport } from '../../core/models/target-prices';

type System = 'classic' | 'mantra';

@Component({
  selector: 'app-prices',
  imports: [
    LucideAngularModule,
    MatButton,
    MatButtonToggle,
    MatButtonToggleGroup,
    MatCard,
    MatChip,
    MatChipSet,
    MatProgressBar,
  ],
  templateUrl: './prices.html',
  styleUrl: './prices.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PricesComponent implements OnInit {
  private readonly service = inject(PricingService);
  private readonly destroyRef = inject(DestroyRef);

  readonly system = signal<System>('classic');
  readonly report = signal<TargetPricesReport | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);
  /** True while the *storing* call is in flight. Separate from `loading` because one of
   *  them is a page load and the other is an action the operator asked for. */
  readonly storing = signal(false);

  ngOnInit(): void {
    this.load();
  }

  setSystem(system: System): void {
    if (this.system() === system) return;
    this.system.set(system);
    this.load();
  }

  /** Read the report. Writes nothing — see `PricingService.getReport`. */
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

  /**
   * Fit and store. The one thing on this page that writes, and it is now a button the
   * operator presses rather than a side effect of arriving.
   */
  store(): void {
    if (this.storing()) return;
    this.storing.set(true);
    this.errorMsg.set(null);
    this.service
      .storeReport(this.system())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.report.set(report);
          this.storing.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.storing.set(false);
        },
      });
  }

  flagList(): { flag: string; count: number }[] {
    const counts = this.report()?.flag_counts ?? {};
    return Object.entries(counts).map(([flag, count]) => ({ flag, count }));
  }
}
