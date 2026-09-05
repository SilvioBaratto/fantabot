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
import { JournalPage } from '../../core/models/journal';
import { LegaOverview } from '../../core/models/lega';
import { RoomCheck } from '../../core/models/room';

/** One page of the journal. The server bounds it too; this is the client's request. */
const JOURNAL_PAGE = 100;

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

  /**
   * The room journal — the CLI's record of an evening, not the app's.
   *
   * Fetched on open and not on arrival: 1.6 MB parsed on every visit to a page whose
   * subject is the plan is a cost nobody asked for, and a journal is a post-mortem.
   */
  readonly journalOpen = signal(false);
  readonly journal = signal<JournalPage | null>(null);
  readonly journalLoading = signal(false);
  readonly journalOffset = signal(0);
  readonly journalError = signal<string | null>(null);

  readonly players = computed(() =>
    [...(this.plan()?.players ?? [])].sort((a, b) => b.price - a.price),
  );

  /** `101–200 of 5,192`, computed from the page the server actually answered with. */
  readonly journalRange = computed(() => {
    const page = this.journal();
    if (!page || !page.rows.length) return '';
    return `${page.offset + 1}–${page.offset + page.rows.length} of ${page.total}`;
  });

  readonly hasNextPage = computed(() => {
    const page = this.journal();
    return !!page && page.offset + page.rows.length < page.total;
  });

  readonly hasPreviousPage = computed(() => this.journalOffset() > 0);

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

  /** Open the journal, fetching its first page the first time. Closing keeps it. */
  toggleJournal(): void {
    const open = !this.journalOpen();
    this.journalOpen.set(open);
    if (open && this.journal() === null) this.loadJournal(0);
  }

  nextPage(): void {
    if (!this.hasNextPage()) return;
    const page = this.journal();
    this.loadJournal(this.journalOffset() + (page?.limit ?? JOURNAL_PAGE));
  }

  previousPage(): void {
    if (!this.hasPreviousPage()) return;
    const page = this.journal();
    this.loadJournal(Math.max(0, this.journalOffset() - (page?.limit ?? JOURNAL_PAGE)));
  }

  private loadJournal(offset: number): void {
    if (this.journalLoading()) return;
    this.journalLoading.set(true);
    this.journalError.set(null);
    this.asta
      .getJournal(offset, JOURNAL_PAGE)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          this.journal.set(page);
          // The server's own offset, not the one asked for: it clamps, and a viewer that
          // paged from its request would drift one page per clamp.
          this.journalOffset.set(page.offset);
          this.journalLoading.set(false);
        },
        error: () => {
          this.journalError.set('Could not reach the API.');
          this.journalLoading.set(false);
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
