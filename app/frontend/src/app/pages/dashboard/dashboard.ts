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
import { MatCardModule } from '@angular/material/card';
import { MatProgressBar } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { LucideAngularModule } from 'lucide-angular';

import { LegaService } from '../../core/api/lega.service';
import { LegaOverview, TeamRoster } from '../../core/models/lega';

@Component({
  selector: 'app-dashboard',
  imports: [LucideAngularModule, MatButton, MatCardModule, MatProgressBar, MatTableModule],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardComponent implements OnInit {
  private readonly service = inject(LegaService);
  private readonly destroyRef = inject(DestroyRef);

  /** Column order for the roster `mat-table`. Presentation only — no read of it decides
   * anything, and the four keys match the `matColumnDef`s in the template. */
  readonly teamColumns = ['team', 'spent', 'left', 'players'];

  readonly leagues = signal<LegaOverview[]>([]);
  readonly loading = signal(true);
  readonly loaded = signal(false);
  readonly errorMsg = signal<string | null>(null);

  readonly expandedId = signal<number | null>(null);
  readonly rosters = signal<TeamRoster[]>([]);
  readonly rostersLoading = signal(false);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getLeagues()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (leagues) => {
          this.leagues.set(leagues);
          this.loaded.set(true);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API');
          this.loading.set(false);
        },
      });
  }

  toggleTeams(leagueId: number): void {
    if (this.expandedId() === leagueId) {
      this.expandedId.set(null);
      return;
    }
    this.expandedId.set(leagueId);
    this.rosters.set([]);
    this.rostersLoading.set(true);
    this.service
      .getRosters(leagueId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (rosters) => {
          this.rosters.set(rosters);
          this.rostersLoading.set(false);
        },
        error: () => this.rostersLoading.set(false),
      });
  }

  roleBand(overview: LegaOverview): string | null {
    if (!overview.min_roles || !overview.max_roles) return null;
    return `${overview.min_roles.join('/')} → ${overview.max_roles.join('/')}`;
  }
}
