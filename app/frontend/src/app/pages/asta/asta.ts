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
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatDividerModule } from '@angular/material/divider';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, catchError, interval, startWith, switchMap, takeWhile } from 'rxjs';

import { AstaService } from '../../core/api/asta.service';
import { ExclusionsService } from '../../core/api/exclusions.service';
import { JobsService } from '../../core/api/jobs.service';
import { LegaService } from '../../core/api/lega.service';
import { AstaPlan } from '../../core/models/asta-plan';
import { Exclusion } from '../../core/models/exclusion';
import { JournalPage, JournalRow } from '../../core/models/journal';
import { LegaOverview } from '../../core/models/lega';
import { Advisory, RoomCheck } from '../../core/models/room';
import { WindowSizeClassService } from '../../core/window-size-class';

/** One page of the journal. The server bounds it too; this is the client's request. */
const JOURNAL_PAGE = 100;

/**
 * The supervised child's kind, spelled as `room.py` spells it on `registry.start`.
 *
 * A second spelling here would never match and the page would silently never reattach —
 * which looks exactly like "no watch is running" and is how a second watch gets started
 * on a room that already has one.
 */
const WATCH_KIND = 'asta-watch';

/**
 * The bidding child's kind, spelled as `room_bid.py` spells it.
 *
 * A separate kind and not a flag on the watch: the page renders a different banner over a
 * run that can spend credits, and `GET /jobs` is the only thing a reopened tab has to tell
 * them apart. A page that reattached only `asta-watch` would offer to start a second run on
 * a room that already has one bidding in it.
 */
const BID_KIND = 'asta-bid';

/** The two kinds this page attaches, newest-first order irrelevant: only one can be live. */
const LIVE_KINDS = [BID_KIND, WATCH_KIND];

/**
 * How often the tail is read. The room's own poll is 2 s (`interface/asta.py:518`), so a
 * faster tail re-reads a file nothing has appended to; a slower one shows a lot after the
 * raise timer has run out on it.
 */
const TAIL_MS = 2000;

/**
 * Rows per tail. One row per cycle at 2 s, so this is about three minutes of catch-up —
 * enough to survive a tab that was backgrounded, bounded well below the server's 500.
 */
const TAIL_LIMIT = 100;

/**
 * Over this, the cycle is reported as slow rather than merely printed.
 *
 * It is the room's own poll interval, not a taste: a cycle whose *work* takes longer than
 * the gap it was supposed to leave is one whose cadence is now set by the work. The
 * measured case is the per-lot re-solve, which stalled the loop up to 72 s at lot changes
 * — and on screen a stalled loop and a calm room are the same picture.
 */
const SLOW_CYCLE_MS = 2000;

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
 *
 * `fallback` is a parameter rather than one sentence baked in. Three surfaces now share
 * this — the two exclusion writes and the room watch — and a refusal that named the wrong
 * one of them would be a screen telling the operator about an act they did not perform.
 */
function refusalOf(err: unknown, fallback: string): string {
  const response = err as { status?: number; error?: { detail?: unknown } };
  if (response?.status === 0) {
    return 'Could not reach the API. Make sure fantabot-app is running.';
  }
  const detail = response?.error?.detail;
  return typeof detail === 'string' && detail ? detail : fallback;
}

/** The two exclusion writes share one, because they are refused by one function. */
const EXCLUSION_FALLBACK = 'The exclusion was refused and the reason did not come back.';

@Component({
  selector: 'app-asta',
  imports: [
    LucideAngularModule,
    DecimalPipe,
    MatButtonModule,
    MatButtonToggleModule,
    MatCardModule,
    MatCheckboxModule,
    MatDividerModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatTableModule,
    RouterLink,
  ],
  templateUrl: './asta.html',
  styleUrl: './asta.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AstaComponent implements OnInit {
  private readonly lega = inject(LegaService);
  private readonly asta = inject(AstaService);
  private readonly exclusionsApi = inject(ExclusionsService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);

  /** Where focus goes when a page turn takes away the button that asked for it. */
  private readonly journalBody = viewChild<ElementRef<HTMLElement>>('journalBody');
  /** Where focus goes when a "Try again" removes itself by starting the load it retries. */
  private readonly pageHeading = viewChild<ElementRef<HTMLElement>>('pageHeading');

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
  /**
   * The plan request itself failed. Its own state because nothing else says it: `plan()`
   * stays null and `planLoading()` goes false, which the template used to render as an
   * empty pane — no card, no message, under a lega that looked selected and fine.
   */
  readonly planError = signal(false);
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
   * The live room — the same journal, read from the other end while it is still growing.
   *
   * **The journal is the channel, not stdout.** `asta room` paints a Rich `Live`, and a
   * `Live` on a pipe renders to nobody, so the supervised child says what it is doing
   * only by appending a row per cycle to the file this tails. Nothing here bids: the
   * watch is started without `--arm` and the page has no control that could add it.
   */
  readonly watchJobId = signal<string | null>(null);
  readonly watchStarting = signal(false);
  /** The server's refusal, verbatim — `parse_room_url`'s own sentence on a 400. */
  readonly watchError = signal<string | null>(null);

  /**
   * Which of the two children is attached. `null` when none is.
   *
   * One id for both, because only one run is ever shown: they tail the same journal, and a
   * second set of signals would be a second answer to "what is on screen".
   */
  readonly runKind = signal<'watch' | 'bid' | null>(null);

  /**
   * The rolling advisory over the checked room's sale ledger.
   *
   * Read-only and on demand rather than polled: it re-solves the plan once per sale, which
   * is the expensive half of `GET /asta/plan` — and the room view beside it is already
   * showing the *bidder's* per-cycle decision. This is what an operator bidding by hand
   * reads when there is no bidder.
   */
  readonly advisory = signal<Advisory | null>(null);
  readonly advisoryLoading = signal(false);
  /** The server's refusal, verbatim. Never composed here. */
  readonly advisoryError = signal<string | null>(null);

  /**
   * The arming intent, restated on every request that could act.
   *
   * **Starts off and is never restored from anywhere.** `application/arming`'s rule and the
   * reason for it: a page can be reloaded, restored by the session manager, or left open
   * overnight, and none of those may carry an arming decision forward. The server refuses a
   * request that does not say at all — a 422, not a dry run.
   */
  readonly armRequested = signal(false);
  readonly bidStarting = signal(false);

  /**
   * True while any live run is attached or being started, by either button.
   *
   * **Both buttons used to guard only themselves.** `watchJobId()` is still null while the
   * first POST is in flight, so a click on *Bid in room* followed by one on *Watch room*
   * inside that window started **both** children; whichever answered second overwrote
   * `watchJobId`, and if that was the watch, the header read "Watching", Stop addressed the
   * watch, and an armed bidder ran on invisible — unstoppable from this page. Two tails also
   * shared `liveSince`, so each poll advanced the cursor for both and the Lot pane skipped
   * cycles.
   */
  readonly liveBusy = computed(
    () => this.watchStarting() || this.bidStarting() || this.watchJobId() !== null,
  );

  /**
   * True while a stop request is in flight.
   *
   * The label only flips to "Stop run" once the first response lands, so two fast clicks
   * both read "Stop" and both were sent. Escalation is per request and server-side, so
   * stage one (disarm, keep drawing) and stage two (end it) fired together and the operator
   * lost the live view in one gesture — the exact thing two stages exist to prevent.
   */
  readonly stopping = signal(false);
  /** What the server answered about the locks — never what this page asked for. */
  readonly runArmed = signal(false);
  /** Every shut lock, by name, in the order an operator would fix them. */
  readonly runClosed = signal<string[]>([]);
  /** The same facts as one line, for the reader with no controls to mark. */
  readonly runReason = signal('');
  /**
   * The band the run was started with, as the server echoed it back.
   *
   * Not read off `room()`: that is what the *check* found, and the two are only the same
   * number because the route now sends it. Showing the run's own copy is what would catch
   * them diverging again.
   */
  readonly runBand = signal<{ size: number | null; provenance: string } | null>(null);

  /**
   * How many stops this page has sent for the attached run. 0, 1 or 2.
   *
   * It reports what was **asked**, never what happened: the escalation state lives in the
   * flag file on the server, which is what makes it survive this tab reloading. The job's
   * own status is what says the run ended, and until it does the view keeps drawing — a
   * stop that killed the view along with the bidding would leave the operator blind at the
   * exact moment they have to bid by hand.
   */
  readonly stopsAsked = signal(0);

  /**
   * Whether a status poll is already running for the attached job.
   *
   * A plain field, not a signal: nothing renders it, and the only thing it decides is that
   * a second stop does not start a second interval — which would double the request rate
   * for every click after the first.
   */
  private statusWatched = false;

  /**
   * The newest row the tail has seen. One slot, not a list: the panes are a frame, and
   * the evening's list is the journal section below.
   *
   * It is **kept after the watch ends**, and that is `error_overlay`'s trade: blanking
   * the screen would take the walk-away away at the exact moment the operator has to bid
   * by hand instead.
   */
  readonly liveRow = signal<JournalRow | null>(null);
  /** Where the next tail resumes. The server's `next_index`, never a count kept here. */
  readonly liveSince = signal(0);
  /**
   * Consecutive polls that brought no new row.
   *
   * The other half of `cycle_ms`: that one says the last cycle was slow, this says there
   * has not been a cycle. A stopped loop and a quiet room are the same picture on screen,
   * and only one of them is still bidding.
   */
  readonly quietPolls = signal(0);
  /** A poll that did not answer. The frame below it is stale, and this is what says so. */
  readonly tailError = signal<string | null>(null);

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

  /**
   * What each walk-away kind means, once, in the order the targets first use it.
   *
   * The provenance is one of a closed set of `kind: explanation` strings
   * (`application/plan_request.py`), and it was printed whole under every row — thirty
   * copies of two sentences, which buried the one number per row that matters. The row
   * keeps the kind; the sentence is said here.
   */
  readonly provenanceLegend = computed(() => {
    const seen = new Map<string, string>();
    for (const player of this.players()) {
      const { kind, meaning } = splitProvenance(player.walk_away_provenance);
      if (meaning && !seen.has(kind)) seen.set(kind, meaning);
    }
    return [...seen].map(([kind, meaning]) => ({ kind, meaning }));
  });

  /**
   * The lot on the block, folded for comparison — or null when no row names one.
   *
   * The join between the two halves of this view, and the only one available: the journal
   * carries one lot per row and the listone lives in the child's `RoomTracker`, which is
   * written down nowhere. So the LISTONE pane is the *plan's* targets, and this is what
   * marks which of them is up.
   *
   * Matched on the name because the ids do not meet: the journal's `lot` is a FantaLab
   * uuid and the plan's `player_id` is a fantacalcio id — defect B1 is exactly that
   * mismatch, and it cost a whole evening of "not a target, hold".
   */
  readonly onTheBlock = computed(() => {
    const name = this.liveRow()?.name;
    return name ? name.trim().toLowerCase() : null;
  });

  /** Slower than the poll it was supposed to fit inside. Null timing is not slow. */
  readonly cycleSlow = computed(() => {
    const ms = this.liveRow()?.cycle_ms;
    return ms !== null && ms !== undefined && ms > SLOW_CYCLE_MS;
  });

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
    this.reattachWatch();
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
          this.excludeError.set(refusalOf(err, EXCLUSION_FALLBACK));
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
          this.withdrawError.set(refusalOf(err, EXCLUSION_FALLBACK));
          this.withdrawing.set(null);
        },
      });
  }

  /** The short half of a walk-away provenance, sentence-cased for the row. */
  provenanceKind(provenance: string): string {
    return splitProvenance(provenance).kind;
  }

  /** `bid` → `Bid`. The journal writes lower case; the pane reads it as a label. */
  decisionLabel(decision: string | null): string {
    return sentenceCase(decision ?? 'waiting');
  }

  retryLeagues(): void {
    this.focusHeading();
    this.loadLeagues();
  }

  retryPlan(): void {
    const id = this.selectedId();
    if (id === null) return;
    this.focusHeading();
    this.select(id);
  }

  private focusHeading(): void {
    this.pageHeading()?.nativeElement.focus();
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
            seat_user_id: null,
            roster_size: null,
            roster_provenance: '',
          });
          this.roomChecking.set(false);
        },
      });
  }

  // -- the live room ------------------------------------------------------------------

  /**
   * Start a watch over the room in the link field, and begin tailing it.
   *
   * The same field the room check reads, deliberately: one link on the page, and an
   * operator who has just checked a room does not paste it again to watch it.
   *
   * **A subprocess, not a request.** The run outlives the tab, which is what makes
   * closing it harmless and is also why `reattachWatch` exists — the page must find a
   * live watch rather than offer a second one.
   */
  watchRoom(): void {
    const url = this.roomUrl().trim();
    if (!url || this.liveBusy()) return;
    this.watchStarting.set(true);
    this.watchError.set(null);
    this.asta
      .watchRoom(url)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (started) => {
          this.watchStarting.set(false);
          // A watch cannot arm and says so: the route sends no `--arm` and there is no
          // control that could add one. Reset rather than left over from a previous bid.
          this.runArmed.set(false);
          this.runClosed.set([]);
          this.runReason.set('');
          // A watch plans nothing, so it has no band to report.
          this.runBand.set(null);
          this.attach(started.job_id, 'watch');
        },
        error: (err: unknown) => {
          // The 400's `detail` is `parse_room_url`'s own sentence and already names what
          // to paste instead. A message composed here would be a second opinion about a
          // link the server has already ruled on.
          this.watchError.set(
            refusalOf(err, 'The watch was refused and the reason did not come back.'),
          );
          this.watchStarting.set(false);
        },
      });
  }

  /**
   * Ask for the advisory over the room that has already resolved.
   *
   * **Refuses before a check**, and that is not politeness: the shard, the seat and the
   * room's shape all come from the check, and a request without them would carry the
   * defaults — an advisory priced against another lega's game, which is worse than none.
   */
  loadAdvisory(): void {
    const room = this.room();
    if (!room || room.outcome !== 'resolved' || this.advisoryLoading()) return;
    if (
      room.fantaleague_id === null ||
      room.shard === null ||
      room.seat_team_id === null ||
      room.asta_type === null
    ) {
      return;
    }
    this.advisoryLoading.set(true);
    this.advisoryError.set(null);
    const credits = room.num_credits ?? 500;
    this.asta
      .advisory({
        league: room.fantaleague_id,
        db: room.shard,
        team: room.seat_team_id,
        listone: room.asta_type,
        teams: room.num_teams ?? 8,
        credits,
        budget: credits,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (body) => {
          this.advisoryLoading.set(false);
          this.advisory.set(body);
          // The refusal is the server's own sentence. An outage rendered as "no targets"
          // is a false statement, not a missing one — and it is the state in which an
          // operator decides they have nothing to chase.
          this.advisoryError.set(body.outcome === 'advised' ? null : body.reason);
        },
        error: (err: unknown) => {
          this.advisoryLoading.set(false);
          this.advisoryError.set(
            refusalOf(err, 'The advisory was refused and the reason did not come back.'),
          );
        },
      });
  }

  setArmRequested(arm: boolean): void {
    this.armRequested.set(arm);
  }

  /**
   * Start a **bidding** run over the room in the link field.
   *
   * The same field and the same tail as the watch: what differs is that this child is given
   * the room's own shape and, when both locks are open, `--arm`. Nothing here decides
   * whether it may act — the route asks `application/arming` and the child re-reads the
   * ambient lock on every write, which is what makes editing `.env` mid-evening disarm a
   * run this page started.
   *
   * A room that does not resolve starts nothing and says which of the four reasons it was.
   * The reason is the server's, verbatim: a sentence composed here would be a second
   * opinion about a room the server has already ruled on.
   */
  bidRoom(): void {
    const url = this.roomUrl().trim();
    if (!url || this.liveBusy()) return;
    this.bidStarting.set(true);
    this.watchError.set(null);
    this.asta
      .bidRoom(url, this.armRequested())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (started) => {
          this.bidStarting.set(false);
          this.runArmed.set(started.armed);
          this.runClosed.set(started.closed);
          this.runReason.set(started.reason);
          this.runBand.set({
            size: started.roster_size,
            provenance: started.roster_provenance,
          });
          if (started.outcome !== 'started') {
            this.watchError.set(started.reason || 'The room would not resolve.');
            return;
          }
          this.attach(started.job_id, 'bid');
        },
        error: (err: unknown) => {
          this.watchError.set(
            refusalOf(err, 'The run was refused and the reason did not come back.'),
          );
          this.bidStarting.set(false);
        },
      });
  }

  /**
   * Ask the attached run to stop, one stage further than last time.
   *
   * **It does not detach.** The first stop on an armed run disarms it and leaves it
   * drawing — that is the whole reason the gesture has two stages, and 3.9c's own
   * criterion: *a stop that kills the view along with the bidding leaves the operator blind
   * mid-auction.* What ends the view is `watchStatus` seeing the job finish, which is also
   * what covers the run that had nothing to disarm and left on the first request.
   *
   * A 409 is the server saying the job has no way to be stopped — not that stopping failed
   * — so it is reported, the job stays attached, and the counter does not move: the
   * escalation the next click means is the server's, and nothing was escalated.
   */
  stopRun(): void {
    const jobId = this.watchJobId();
    if (jobId === null || this.stopping()) return;
    this.stopping.set(true);
    this.jobs
      .stop(jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.stopping.set(false);
          // Clamped at 2: there is no third stage. `request_stop` stays at `exit`, so a
          // counter past 2 only stops matching `stopsAsked() === 1` and silently takes the
          // disarm note off the screen.
          this.stopsAsked.update((asked) => Math.min(asked + 1, 2));
        },
        error: (err: unknown) => {
          this.stopping.set(false);
          this.watchError.set(refusalOf(err, 'The job would not stop and did not say why.'));
        },
      });
  }

  /**
   * Poll the job's own status until it is no longer running, then detach.
   *
   * Started by the first stop and only then: before one, the tail is the whole cost of the
   * view, and a second request per tick for the three hours an auction lasts buys nothing
   * — nothing but a stop is going to end the run.
   *
   * The frame is deliberately **not** cleared on detach. `error_overlay`'s trade: the last
   * walk-away on screen is what the operator bids by hand with.
   */
  private watchStatus(jobId: string): void {
    if (this.statusWatched) return;
    this.statusWatched = true;
    // Started from `attach`, not from `stopRun`. A child that ends **by itself** — it
    // crashes, the room closes, somebody stops it from a terminal — left the 2 s tail
    // running for the life of the tab with the header still claiming "Bidding", and because
    // both start methods return early on `watchJobId() !== null`, the operator could not
    // start another run without reloading. The click did nothing and said nothing.
    interval(TAIL_MS)
      .pipe(
        takeWhile(() => this.watchJobId() === jobId),
        switchMap(() => this.jobs.get(jobId).pipe(catchError(() => EMPTY))),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((status) => {
        if (status.status !== 'running') this.detach();
      });
  }

  /** Let go of the run. The frame it left stays on screen — see `watchStatus`. */
  private detach(): void {
    this.watchJobId.set(null);
    this.runKind.set(null);
    this.stopsAsked.set(0);
    this.statusWatched = false;
  }

  /**
   * Pick up a watch that was already running when this page loaded.
   *
   * `GET /jobs` is the source of truth, the same way the harvest page reattaches its
   * three children. A listing that cannot be read is not an error worth showing: nothing
   * the operator asked for has failed, and a red banner on arrival would be about the
   * poll rather than about them.
   */
  private reattachWatch(): void {
    // `JobsService.running` is already filtered to the jobs that are running: `GET /jobs`
    // lists everything this session has ever run, and tailing a `done` row would show a
    // room that closed hours ago and call it live.
    this.jobs.running(this.destroyRef).subscribe((jobs) => {
      const live = jobs.find((job) => LIVE_KINDS.includes(job.kind));
      if (!live) return;
      // `armed` off the job itself, not inferred and not sniffed out of the log. Without
      // it a reloaded tab drew a live armed bidder exactly as it draws a rehearsal: the
      // ARMED banner needs `runArmed()` and the dry-run note needs `runReason()`, so both
      // were suppressed and the page said nothing at all at the one moment it matters.
      this.runArmed.set(live.armed === true);
      this.runClosed.set([]);
      // `GET /jobs` does not carry the band, and inventing one from the current room
      // check would claim the run used a number nobody has checked it did.
      this.runBand.set(null);
      this.runReason.set(
        live.armed === null || live.armed === undefined
          ? ''
          : live.armed
            ? ''
            : 'this run was started without arming.',
      );
      this.attach(live.id, live.kind === BID_KIND ? 'bid' : 'watch');
    });
  }

  /**
   * Hold the job and start the tail.
   *
   * The first read is **synchronous on subscribe**, not one interval late: an evening
   * already under way has rows to show now, and two seconds of blank panes is the state
   * this view exists to prevent.
   */
  private attach(jobId: string, kind: 'watch' | 'bid'): void {
    // Refused rather than overwritten. The `liveBusy` guard closes the window on the two
    // buttons; this closes it on anything else that reaches here, including a reattach
    // landing while a start is in flight.
    if (this.watchJobId() !== null) return;
    this.watchJobId.set(jobId);
    this.runKind.set(kind);
    this.stopsAsked.set(0);
    // A new run starts from a blank frame. Keeping the last one past *detach* is deliberate
    // — the walk-away is what an operator bids by hand with — but carrying it into a new
    // attachment drew the previous evening's lot under this one's header, with its quiet
    // count intact: "No new row in 34 poll(s)" on a run one second old.
    this.liveRow.set(null);
    this.liveSince.set(0);
    this.quietPolls.set(0);
    this.tailError.set(null);
    this.watchStatus(jobId);
    interval(TAIL_MS)
      .pipe(
        startWith(0),
        // Read per tick, never captured: this is what a stop switches off, and a
        // predicate bound at subscribe time would keep polling a job that ended.
        takeWhile(() => this.watchJobId() !== null),
        switchMap(() =>
          this.asta.followJournal(this.liveSince(), TAIL_LIMIT).pipe(
            catchError(() => {
              // One failed read is not the end of the evening, so the tick survives it.
              // What must not survive it is the impression that the frame below is
              // current — stale is still the right thing to draw, and this banner is the
              // only thing that says it is stale.
              this.tailError.set('Could not reach the API.');
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((page) => {
        this.tailError.set(null);
        // The server's position, not a count kept here: `next_index` is the last row
        // *parsed*, which a torn line makes smaller than the file's length.
        this.liveSince.set(page.next_index);
        if (page.rows.length) {
          // Oldest first, so the last one is the newest cycle.
          this.liveRow.set(page.rows[page.rows.length - 1]);
          this.quietPolls.set(0);
        } else {
          this.quietPolls.update((count) => count + 1);
        }
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
    // **An arming intent belongs to one game.** It survived a lega change, so the checkbox
    // stayed ticked and the previous lega's room check stayed on screen, and the next
    // "Bid in room" was armed by a decision somebody made about a different room.
    // `lineup.ts::select` clears its dry run for exactly this reason.
    this.armRequested.set(false);
    this.room.set(null);
    this.advisory.set(null);
    this.advisoryError.set(null);
    this.planError.set(false);
    this.planLoading.set(true);
    this.asta
      .getPlan(leagueId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (plan) => {
          // The lega may have changed again while this was in flight; a late answer for the
          // one the operator has left would draw another game's plan under this heading.
          if (this.selectedId() !== leagueId) return;
          this.plan.set(plan);
          this.planLoading.set(false);
        },
        // Guarded like `next`, and for the same reason: a late failure for the lega the
        // operator has left used to switch off the spinner of the one they are waiting on.
        error: () => {
          if (this.selectedId() !== leagueId) return;
          this.planLoading.set(false);
          this.planError.set(true);
        },
      });
  }
}

function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** `"hold: a substitute exists …"` → `{ kind: 'Hold', meaning: 'a substitute exists …' }`.
 * A provenance with no colon is all kind, so an unforeseen string is shown, never dropped. */
function splitProvenance(provenance: string): { kind: string; meaning: string | null } {
  const at = provenance.indexOf(': ');
  if (at < 0) return { kind: sentenceCase(provenance), meaning: null };
  return {
    kind: sentenceCase(provenance.slice(0, at)),
    meaning: provenance.slice(at + 2),
  };
}
