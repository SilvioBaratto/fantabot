import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { Subscription } from 'rxjs';
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
  /** The one request that may still write to the page. A fit is slow, so the operator can
   *  change listone while it runs; a response for the old listone must never land under
   *  the new toggle. Unsubscribing drops the response, not the server-side write. */
  private inflight: Subscription | null = null;

  readonly system = signal<System>('classic');
  readonly report = signal<TargetPricesReport | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);
  /** True while the *storing* call is in flight. Separate from `loading` because one of
   *  them is a page load and the other is an action the operator asked for. */
  readonly storing = signal(false);

  ngOnInit(): void {
    this.destroyRef.onDestroy(() => this.supersede());
    this.load();
  }

  private supersede(): void {
    this.inflight?.unsubscribe();
    this.inflight = null;
    this.storing.set(false);
  }

  setSystem(system: System): void {
    if (this.system() === system) return;
    this.system.set(system);
    this.load();
  }

  /** Read the report. Writes nothing — see `PricingService.getReport`. */
  load(): void {
    this.supersede();
    this.loading.set(true);
    this.errorMsg.set(null);
    this.inflight = this.service.getReport(this.system()).subscribe({
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
    this.supersede();
    this.loading.set(false);
    this.storing.set(true);
    this.errorMsg.set(null);
    this.inflight = this.service.storeReport(this.system()).subscribe({
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
