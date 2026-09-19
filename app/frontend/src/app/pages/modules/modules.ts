import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatCard, MatCardContent, MatCardHeader, MatCardTitle } from '@angular/material/card';
import { MatProgressBar } from '@angular/material/progress-bar';
import { LucideAngularModule } from 'lucide-angular';

import { LegalityService } from '../../core/api/legality.service';
import { LegalityGrid } from '../../core/models/legality';

/**
 * Mantra modules — the 11 schemi and the roles each slot accepts.
 *
 * Read-only: the page has no actions, so it has no primary action to rank
 * (`usability/overview.md:105` applies vacuously). Four decisions are worth recording.
 *
 * **It is a feed, not list-detail.** The 11 schemi are peers, each shown whole; nothing
 * expands and nothing is selected, so there is no parent-child relationship for a detail
 * pane to hold (`layout/canonical_examples.md:289`). Columns, not panes, therefore carry
 * the extra width: 1 / 2 / 3 / 4 across the M3 size classes.
 *
 * **A slot is a list item, not a chip.** M3 chips enter information, filter, choose or act;
 * a role token here does none of those, and `mat-chip-set` would drop the slot order behind
 * `role="presentation"` and cost ~110 ripple-bearing chips. The slots stay an `<ol>` built
 * on `--mat-sys-*` tokens — the "custom only for the gaps" case.
 *
 * **The empty grid gets its own state.** `GET /asta/legality` degrades open: a missing
 * `mantra_schemi.json` returns `{schemi: [], roles: []}` with a 200, which used to render as
 * a silent blank page indistinguishable from a working one.
 *
 * **Columns are guarded by the pane's own width, not only the window's.** The shell's rail
 * can be docked expanded (280px) from 840px up, so a window media query alone would put a
 * three-column grid into a 560px pane. Each window step is nested inside a `@container`
 * guard that can only narrow the result (see `modules.scss`).
 */
@Component({
  selector: 'app-modules',
  imports: [
    MatCard,
    MatCardContent,
    MatCardHeader,
    MatCardTitle,
    MatProgressBar,
    LucideAngularModule,
  ],
  templateUrl: './modules.html',
  styleUrl: './modules.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
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
