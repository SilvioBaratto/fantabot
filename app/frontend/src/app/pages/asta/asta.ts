import { DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { LucideAngularModule } from 'lucide-angular';

import { AstaService } from '../../core/api/asta.service';
import { ExclusionsService } from '../../core/api/exclusions.service';
import { LegaService } from '../../core/api/lega.service';
import { AstaPlan } from '../../core/models/asta-plan';
import { Exclusion } from '../../core/models/exclusion';
import { JournalPage } from '../../core/models/journal';
import { LegaOverview } from '../../core/models/lega';
import { RoomCheck } from '../../core/models/room';
import { WindowSizeClassService } from '../../core/window-size-class';

/** One page of the journal. The server bounds it too; this is the client's request. */
const JOURNAL_PAGE = 100;

/**
 * The server's refusal, or a sentence saying the server never answered.
 *
 * A 422 from `POST /db/exclusions` carries `detail` — the wording of
 * `application/exclusions.clean_exclusion`'s own refusal, which is the sentence
 * `fantabot db exclude` prints. Showing that rather than a locally composed message is
 * what makes the page and the terminal agree about *why* something was refused, not
 * merely that it was.
 *
 * `status === 0` is the API being unreachable, which is not a refusal at all and must
 * not be reported as one.
 */
function refusalOf(err: unknown): string {
  const response = err as { status?: number; error?: { detail?: unknown } };
  if (response?.status === 0) {
    return 'Could not reach the API. Make sure fantabot-app is running.';
  }
  const detail = response?.error?.detail;
  return typeof detail === 'string' && detail
    ? detail
    : 'The exclusion was refused and the reason did not come back.';
}

@Component({
  selector: 'app-asta',
  imports: [
    LucideAngularModule,
    DecimalPipe,
    MatButtonModule,
    MatButtonToggleModule,
    MatCardModule,
    MatDividerModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatTableModule,
  ],
  templateUrl: './asta.html',
  styleUrl: './asta.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AstaComponent implements OnInit {
  private readonly lega = inject(LegaService);
  private readonly asta = inject(AstaService);
  private readonly exclusionsApi = inject(ExclusionsService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);

  /** Where focus goes when a page turn takes away the button that asked for it. */
  private readonly journalBody = viewChild<ElementRef<HTMLElement>>('journalBody');

  /**
   * Which button asked for the page currently in flight.
   *
   * Not a signal: nothing renders from it, and it is read once per response. It exists so
   * `rescueFocus` can tell a page turn (where the pressed button may be about to vanish)
   * from the first load, which nobody pressed a pager for.
   */
  private pagedWith: 'newer' | 'older' | null = null;

  /**
   * Presentation only. Read here — rather than left to a CSS media query — because the two
   * tables on this page do not merely *restyle* below their width, they become a different
   * tree: nine journal columns cannot be squeezed into 456px, and the M3 answer to a table
   * that will not fit is a card list, never a horizontal scroll
   * (`layout/breakpoints.md:67`). Everything that only changes style stays in `asta.scss`.
   */
  private readonly size = inject(WindowSizeClassService);

  /** Compact (<600): one column, the plan's targets as cards, the lega picker stacked. */
  readonly compact = computed(() => this.size.current() === 'compact');

  /**
   * The journal's eight columns need roughly 900px, which is more than the pane has below
   * 840px once the shell's docked rail is taken out of the window. Card list up to there.
   */
  readonly journalAsCards = computed(() => !this.size.twoPane());

  readonly playerColumns = ['nome', 'price', 'walkAway'];
  readonly journalColumns = [
    'index',
    'lot',
    'price',
    'walkAway',
    'decision',
    'cap',
    'left',
    'bargain',
  ];

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

  /**
   * The players kept out of every plan.
   *
   * Fetched on arrival rather than behind a toggle, unlike the journal below. The
   * journal is a 1.6 MB post-mortem and the cost is the argument for deferring it; this
   * is single digits of rows, and deferring it would recreate the exact problem it
   * exists to solve — an exclusion is invisible on every other screen, because a plan
   * built without a player looks exactly like a plan built with one nobody wanted.
   */
  readonly exclusions = signal<Exclusion[]>([]);
  /** Set only when the list could not be read. `[]` with no error is a real answer. */
  readonly exclusionsError = signal<string | null>(null);
  readonly exclusionsLoading = signal(true);

  /** The form. Plain signals: three fields, no cross-field rule, no `FormGroup` earned. */
  readonly excludePlayerId = signal('');
  readonly excludeReason = signal('');
  readonly excludeSource = signal('');
  readonly excluding = signal(false);
  /**
   * The server's refusal, verbatim. Never composed here: `clean_exclusion` decides what
   * a valid exclusion is and both surfaces carry its wording, so the sentence on this
   * page is the sentence `fantabot db exclude` prints.
   */
  readonly excludeError = signal<string | null>(null);

  /**
   * Withdrawing one — `fantabot db unexclude`, which is why this control exists at all:
   * §8 Never #4 gives the app no power the CLI lacks, and until the command shipped a
   * typo'd id was removed with `psql` and in no other way.
   *
   * The id currently in flight, so the row that was clicked is the row that shows it.
   */
  readonly withdrawing = signal<number | null>(null);
  /**
   * The row that was removed, kept on screen afterwards. Its reason is the only part of
   * it nothing else in the database holds, so dropping it from the page would make the
   * removal unreversible — `db unexclude` prints the row for the same reason.
   */
  readonly withdrawn = signal<Exclusion | null>(null);
  /** The server's refusal, verbatim — a 404's `detail`, not a sentence composed here. */
  readonly withdrawError = signal<string | null>(null);

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
    this.loadExclusions();
  }

  private loadExclusions(): void {
    this.exclusionsLoading.set(true);
    this.exclusionsApi
      .list()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (body) => {
          this.exclusions.set(body.exclusions);
          // The endpoint's own `error`, which is null on a genuinely empty table. A
          // page that inferred "could not read" from an empty list would say the
          // database is down on every fresh install.
          this.exclusionsError.set(body.error);
          this.exclusionsLoading.set(false);
        },
        error: () => {
          // The API itself did not answer — a sixth thing, outside the endpoint's own
          // vocabulary, so it is said in the app's.
          this.exclusionsError.set('Could not reach the API. Make sure fantabot-app is running.');
          this.exclusionsLoading.set(false);
        },
      });
  }

  setExcludePlayerId(value: string): void {
    this.excludePlayerId.set(value);
  }

  setExcludeReason(value: string): void {
    this.excludeReason.set(value);
  }

  setExcludeSource(value: string): void {
    this.excludeSource.set(value);
  }

  /**
   * Record one exclusion.
   *
   * The fields cross untouched — not trimmed, not defaulted. What makes an exclusion
   * valid is `application/exclusions.clean_exclusion`'s, and a check here would be a
   * second copy of it: one that refuses different things from the command, on a screen
   * whose whole purpose is that the two agree.
   *
   * An unparseable id is the one thing this does decide, and only because there is no
   * number to send: `parseInt('')` is `NaN` and a `NaN` in a JSON body serialises as
   * `null`, which the server would reject as a type error rather than as the refusal
   * the operator needs to read.
   */
  exclude(): void {
    if (this.excluding()) return;
    const playerId = Number.parseInt(this.excludePlayerId().trim(), 10);
    if (!Number.isInteger(playerId)) {
      this.excludeError.set('Enter the fantacalcio player id — a whole number.');
      return;
    }
    this.excluding.set(true);
    this.excludeError.set(null);
    this.exclusionsApi
      .add({
        player_id: playerId,
        reason: this.excludeReason(),
        source: this.excludeSource(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (written) => {
          // The list the server sent back, not the one this page holds plus a row: an
          // upsert over an existing id replaces the reason in place, so a local append
          // would show the player twice with two different reasons.
          this.exclusions.set(written.exclusions);
          this.exclusionsError.set(null);
          this.excludePlayerId.set('');
          this.excludeReason.set('');
          this.excludeSource.set('');
          this.excluding.set(false);
        },
        error: (err: unknown) => {
          this.excludeError.set(refusalOf(err));
          this.excluding.set(false);
        },
      });
  }

  /**
   * Let a player back into every plan.
   *
   * One click and no confirmation, which is the command's behaviour and therefore this
   * page's: what makes the act reversible is the row coming back on the response and
   * staying on the screen, not a dialog in front of it.
   *
   * A 404 is the server saying nothing was excluded under that id. It is shown in the
   * server's own words — the sentence `fantabot db unexclude` prints — so the page and
   * the terminal agree about *why*, not merely that.
   */
  unexclude(playerId: number): void {
    if (this.withdrawing() !== null) return;
    this.withdrawing.set(playerId);
    this.withdrawError.set(null);
    this.exclusionsApi
      .remove(playerId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (body) => {
          // The server's list, as the write takes it: this page shows the table, never
          // its own idea of the table with a row spliced out.
          this.exclusions.set(body.exclusions);
          this.exclusionsError.set(null);
          this.withdrawn.set(body.removed);
          this.withdrawing.set(null);
        },
        error: (err: unknown) => {
          this.withdrawError.set(refusalOf(err));
          this.withdrawing.set(null);
        },
      });
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
    this.pagedWith = 'older';
    this.loadJournal(this.journalOffset() + (page?.limit ?? JOURNAL_PAGE));
  }

  previousPage(): void {
    if (!this.hasPreviousPage()) return;
    const page = this.journal();
    this.pagedWith = 'newer';
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
          this.rescueFocus();
        },
        error: () => {
          this.journalError.set('Could not reach the API.');
          this.journalLoading.set(false);
          this.pagedWith = null;
        },
      });
  }

  /**
   * Keep focus off `<body>` when a page turn reaches a bound.
   *
   * The pager buttons stay enabled while a page is in flight, so the click itself no
   * longer costs focus. What can still take it away is arriving at the last (or first)
   * page: the button just pressed becomes genuinely unavailable and the browser blurs it.
   * Only then, and only for the button that was actually used, focus moves into the rows.
   * `afterNextRender` because the disable happens when the view refreshes, which is after
   * this subscriber returns.
   */
  private rescueFocus(): void {
    const pressed = this.pagedWith;
    this.pagedWith = null;
    if (pressed === null) return;
    const stillUsable = pressed === 'older' ? this.hasNextPage() : this.hasPreviousPage();
    if (stillUsable) return;
    afterNextRender(() => this.journalBody()?.nativeElement.focus(), { injector: this.injector });
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
