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

import { LegaService } from '../../core/api/lega.service';
import { LegaOverview, TeamRoster } from '../../core/models/lega';

@Component({
  selector: 'app-dashboard',
  imports: [LucideAngularModule],
  templateUrl: './dashboard.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class DashboardComponent implements OnInit {
  private readonly service = inject(LegaService);
  private readonly destroyRef = inject(DestroyRef);

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
          this.errorMsg.set('Could not reach the API.');
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
