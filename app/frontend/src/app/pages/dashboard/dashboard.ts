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
import { MatButton } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBar } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { Subscription } from 'rxjs';

import { LegaService } from '../../core/api/lega.service';
import { LegaFormat, LegaOverview, TeamRoster } from '../../core/models/lega';

/** One lega's teams region. Keyed per lega, so a late answer for one lega can only ever
 * land in that lega's region — the single shared `rosters` signal this replaced drew
 * lega A's teams under lega B when B was opened before A's request returned. */
export type RosterState =
  | { readonly kind: 'loading' }
  | { readonly kind: 'ready'; readonly rosters: readonly TeamRoster[] }
  | { readonly kind: 'error' };

/** One group of the role band: a visible label (or none), the name a screen reader hears
 * instead of an abbreviation, and the declared range. */
export interface RoleGroup {
  readonly label: string | null;
  readonly spoken: string | null;
  readonly range: string;
}

/** Group names per format, in the order the lega's `minrl`/`maxrl` arrays list them.
 * Classic keeps the P/D/C/A letters every fantacalcio player reads; Mantra's two groups
 * are goalkeepers and everyone else, which no letter says. */
const GROUPS: Record<LegaFormat, readonly { label: string; spoken: string | null }[]> = {
  classic: [
    { label: 'P', spoken: 'Goalkeepers' },
    { label: 'D', spoken: 'Defenders' },
    { label: 'C', spoken: 'Midfielders' },
    { label: 'A', spoken: 'Forwards' },
  ],
  mantra: [
    { label: 'Goalkeepers', spoken: null },
    { label: 'Outfield', spoken: null },
  ],
};

@Component({
  selector: 'app-dashboard',
  imports: [
    DatePipe,
    LucideAngularModule,
    MatButton,
    MatCardModule,
    MatProgressBar,
    MatTableModule,
    RouterLink,
  ],
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
  /** A failed refresh keeps the cards it already has; only a first load that fails has
   * nothing to show but the error. */
  readonly loadFailed = signal(false);

  readonly expanded = signal<ReadonlySet<number>>(new Set());
  readonly rosterStates = signal<ReadonlyMap<number, RosterState>>(new Map());

  private leaguesRequest: Subscription | null = null;
  private readonly rosterRequests = new Map<number, Subscription>();

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    // A second Refresh supersedes the first rather than racing it.
    this.leaguesRequest?.unsubscribe();
    this.loading.set(true);
    this.leaguesRequest = this.service
      .getLeagues()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (leagues) => {
          this.leagues.set(leagues);
          this.loaded.set(true);
          this.loadFailed.set(false);
          this.loading.set(false);
          this.reloadOpenRosters(leagues);
        },
        error: () => {
          this.loadFailed.set(true);
          this.loading.set(false);
        },
      });
  }

  isExpanded(leagueId: number): boolean {
    return this.expanded().has(leagueId);
  }

  rosterState(leagueId: number): RosterState | undefined {
    return this.rosterStates().get(leagueId);
  }

  /** The teams once they have arrived; null while loading or after a failure. */
  rostersOf(leagueId: number): readonly TeamRoster[] | null {
    const state = this.rosterState(leagueId);
    return state?.kind === 'ready' ? state.rosters : null;
  }

  toggleTeams(leagueId: number): void {
    if (this.isExpanded(leagueId)) {
      this.rosterRequests.get(leagueId)?.unsubscribe();
      this.rosterRequests.delete(leagueId);
      this.expanded.update((ids) => without(ids, leagueId));
      return;
    }
    this.expanded.update((ids) => new Set(ids).add(leagueId));
    this.loadRosters(leagueId);
  }

  /** "Try again" removes itself when the region goes back to loading, so focus is handed
   * to the disclosure button that owns the region instead of dropping to `<body>`. */
  retryRosters(leagueId: number, owner: MatButton): void {
    owner.focus();
    this.loadRosters(leagueId);
  }

  roleBand(overview: LegaOverview): RoleGroup[] | null {
    const { min_roles: min, max_roles: max } = overview;
    if (!min?.length || !max || min.length !== max.length) return null;
    const names = overview.format ? GROUPS[overview.format] : null;
    // A band whose group count disagrees with its format is shown, just not labelled.
    const labelled = names?.length === min.length ? names : null;
    return min.map((low, i) => ({
      label: labelled?.[i].label ?? null,
      spoken: labelled?.[i].spoken ?? null,
      range: low === max[i] ? `${low}` : `${low}–${max[i]}`,
    }));
  }

  private loadRosters(leagueId: number): void {
    this.rosterRequests.get(leagueId)?.unsubscribe();
    this.setRosterState(leagueId, { kind: 'loading' });
    const request = this.service
      .getRosters(leagueId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (rosters) => this.setRosterState(leagueId, { kind: 'ready', rosters }),
        error: () => this.setRosterState(leagueId, { kind: 'error' }),
      });
    this.rosterRequests.set(leagueId, request);
  }

  /** After a refresh, an open region re-reads its teams; one whose lega is gone closes. */
  private reloadOpenRosters(leagues: readonly LegaOverview[]): void {
    const present = new Set(leagues.map((lega) => lega.league_id));
    for (const id of this.expanded()) {
      if (present.has(id)) {
        this.loadRosters(id);
      } else {
        this.rosterRequests.get(id)?.unsubscribe();
        this.rosterRequests.delete(id);
        this.expanded.update((ids) => without(ids, id));
      }
    }
  }

  private setRosterState(leagueId: number, state: RosterState): void {
    this.rosterStates.update((states) => new Map(states).set(leagueId, state));
  }
}

function without(ids: ReadonlySet<number>, id: number): ReadonlySet<number> {
  const next = new Set(ids);
  next.delete(id);
  return next;
}
