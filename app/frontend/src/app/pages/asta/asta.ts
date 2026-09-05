import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { LucideAngularModule } from 'lucide-angular';

import { AstaService } from '../../core/api/asta.service';
import { LegaService } from '../../core/api/lega.service';
import { AstaPlan } from '../../core/models/asta-plan';
import { LegaOverview } from '../../core/models/lega';
import { RoomCheck } from '../../core/models/room';

@Component({
  selector: 'app-asta',
  imports: [LucideAngularModule, DecimalPipe],
  templateUrl: './asta.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class AstaComponent implements OnInit {
  private readonly lega = inject(LegaService);
  private readonly asta = inject(AstaService);
  private readonly destroyRef = inject(DestroyRef);

  readonly leagues = signal<LegaOverview[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly plan = signal<AstaPlan | null>(null);
  readonly loading = signal(true);
  readonly planLoading = signal(false);
  readonly errorMsg = signal<string | null>(null);

  /**
   * The room check. A read of a room's configuration, and the only surface that says
   * whether the stored FantaLab credential still authenticates — until this existed the
   * credential was captured by the Accounts page and read by nothing.
   */
  readonly roomUrl = signal('');
  readonly room = signal<RoomCheck | null>(null);
  readonly roomChecking = signal(false);

  readonly players = computed(() =>
    [...(this.plan()?.players ?? [])].sort((a, b) => b.price - a.price),
  );

  ngOnInit(): void {
    this.loadLeagues();
  }

  loadLeagues(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.lega
      .getLeagues()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (leagues) => {
          this.leagues.set(leagues);
          this.loading.set(false);
          if (leagues.length) this.select(leagues[0].league_id);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  setRoomUrl(value: string): void {
    this.roomUrl.set(value);
  }

  checkRoom(): void {
    const url = this.roomUrl().trim();
    if (!url || this.roomChecking()) return;
    this.roomChecking.set(true);
    this.asta
      .checkRoom(url)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (room) => {
          this.room.set(room);
          this.roomChecking.set(false);
        },
        error: () => {
          // The API itself did not answer. That is a sixth thing, and saying it in the
          // endpoint's own vocabulary keeps the five outcomes meaning what they mean.
          this.room.set({
            outcome: 'unreachable',
            reason: 'Could not reach the API. Make sure fantabot-app is running.',
            fantaleague_id: null,
            shard: null,
            asta_type: null,
            asta_mode: null,
            raise_mode: null,
            num_teams: null,
            num_credits: null,
            seat_team_id: null,
            seat_team_name: null,
            roster_size: null,
            roster_provenance: '',
          });
          this.roomChecking.set(false);
        },
      });
  }

  select(leagueId: number): void {
    this.selectedId.set(leagueId);
    this.plan.set(null);
    this.planLoading.set(true);
    this.asta
      .getPlan(leagueId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (plan) => {
          this.plan.set(plan);
          this.planLoading.set(false);
        },
        error: () => this.planLoading.set(false),
      });
  }
}
