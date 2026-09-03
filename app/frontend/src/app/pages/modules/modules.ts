import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { LegalityService } from '../../core/api/legality.service';
import { LegalityGrid } from '../../core/models/legality';

@Component({
  selector: 'app-modules',
  imports: [],
  templateUrl: './modules.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class ModulesComponent implements OnInit {
  private readonly service = inject(LegalityService);
  private readonly destroyRef = inject(DestroyRef);

  readonly grid = signal<LegalityGrid | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  ngOnInit(): void {
    this.service
      .getGrid()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (grid) => {
          this.grid.set(grid);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }
}
