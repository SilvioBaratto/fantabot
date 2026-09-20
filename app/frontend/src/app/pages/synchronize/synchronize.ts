import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  WritableSignal,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSelectModule } from '@angular/material/select';
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, Observable, catchError, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { JobsService } from '../../core/api/jobs.service';
import { ScrapeService } from '../../core/api/scrape.service';
import { TeamsService } from '../../core/api/teams.service';
import { ScrapeTable } from '../../core/models/scrape';
import { BackfillResult, TeamSnapshotResult } from '../../core/models/teams';
import { IconName } from '../../icons';

/** The job kinds this page owns. `GET /jobs` lists every kind; only these belong here. */
const KIND = 'lega-sync';
const SCRAPE_KIND = 'db-scrape';

/**
 * One job's worth of page state, so the poll is written once.
 *
 * The lega sync and the scrape are different shapes of work — eight reads on a daemon
 * thread, and a supervised child fetching a live site for minutes — but a poll is a poll,
 * and two copies of `?since=` bookkeeping is two places for a reattach to stop appending
 * and start replacing. `pages/harvest/harvest.ts` names the same record `JobPanel`.
 */
interface JobPanel {
  readonly running: WritableSignal<boolean>;
  readonly lines: WritableSignal<string[]>;
  readonly status: WritableSignal<string>;
  readonly ok: WritableSignal<boolean | null>;
  readonly jobId: WritableSignal<string | null>;
}

/**
 * How the run reads to a person, as opposed to how the job registry spells it.
 *
 * `partial` is the one that has to stay separate: `lega_sync.collect` fails **per read**,
 * so eight reads can end with six landed and two not, and the job still returns. Folding
 * that into `failed` would say nothing was written when six tables were, and folding it
 * into `complete` would hide that a sync has to be re-run.
 */
export type SyncOutcome = 'idle' | 'running' | 'complete' | 'partial' | 'failed';

const OUTCOME_LABEL: Record<SyncOutcome, string> = {
  idle: '',
  running: 'Running',
  complete: 'Completed',
  partial: 'Completed with failures',
  failed: 'Failed',
};

/** `idle` renders no status row at all; its entry is here only to keep the record total. */
const OUTCOME_ICON: Record<SyncOutcome, IconName> = {
  idle: 'CheckCircle',
  running: 'Loader2',
  complete: 'CheckCircle',
  partial: 'TriangleAlert',
  failed: 'CircleX',
};

/**
 * What a transport failure becomes. The server's own vocabulary, so the page renders one
 * branch: a 500, a dropped connection and a named `unreachable` are the same fact to the
 * operator — the app could not ask — and the same remedy.
 */
const UNREACHABLE_SNAPSHOT = (leagueId: number): TeamSnapshotResult => ({
  outcome: 'unreachable',
  reason: 'Could not reach the API. Make sure fantabot-app is running.',
  league_id: leagueId,
  team_id: null,
  nome: '',
  owner: '',
  credits_initial: null,
  credits_spent: null,
  credits_remaining: null,
});

const UNREACHABLE_BACKFILL: BackfillResult = {
  outcome: 'unreachable',
  reason: 'Could not reach the API. Make sure fantabot-app is running.',
  changed: 0,
};

@Component({
  selector: 'app-synchronize',
  imports: [
    LucideAngularModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatSelectModule,
  ],
  templateUrl: './synchronize.html',
  styleUrl: './synchronize.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SynchronizeComponent {
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
  private readonly scrape = inject(ScrapeService);
  private readonly teams = inject(TeamsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly leagueId = signal<number | null>(null);
  readonly running = signal(false);
  readonly lines = signal<string[]>([]);
  readonly jobStatus = signal<string>('');
  readonly jobOk = signal<boolean | null>(null);
  readonly errorMsg = signal<string | null>(null);

  /**
   * The two one-shot team commands. Each holds its **whole** answer — outcome, reason
   * and figures — in one signal rather than a running flag beside an error string
   * beside a result: a transport failure is turned into an `unreachable` outcome on the
   * way in, so the template has one branch to render and no pair of signals that can
   * disagree about what happened.
   *
   * They are deliberately not in `lines()`. That log belongs to a lega-sync job polled
   * from `GET /jobs/{id}`, and a request/response result appended to it would read as a
   * read that landed during a sync nobody started.
   */
  readonly snapshotting = signal(false);
  readonly snapshot = signal<TeamSnapshotResult | null>(null);
  readonly backfilling = signal(false);
  readonly backfill = signal<BackfillResult | null>(null);

  /**
   * The scrape card. A supervised child, so it has a job's worth of state rather than
   * one result signal: `db scrape voti` is ~38 polite GETs per season and the operator
   * watches it, which is the line `endpoints/teams.py` drew between the two.
   */
  readonly scrapeTables = signal<ScrapeTable[]>([]);
  readonly currentSeason = signal('');
  readonly scrapeTable = signal('');
  readonly scrapeSeasons = signal<string[]>([]);
  readonly scrapeRunning = signal(false);
  readonly scrapeLines = signal<string[]>([]);
  readonly scrapeStatus = signal('');
  readonly scrapeOk = signal<boolean | null>(null);
  readonly scrapeJobId = signal<string | null>(null);
  readonly scrapeError = signal<string | null>(null);

  private readonly legaPanel: JobPanel = {
    running: this.running,
    lines: this.lines,
    status: this.jobStatus,
    ok: this.jobOk,
    jobId: signal<string | null>(null),
  };

  private readonly scrapePanel: JobPanel = {
    running: this.scrapeRunning,
    lines: this.scrapeLines,
    status: this.scrapeStatus,
    ok: this.scrapeOk,
    jobId: this.scrapeJobId,
  };

  /** The chosen table's row from the picker, or null before one is chosen. */
  readonly selectedScrapable = computed<ScrapeTable | null>(
    () => this.scrapeTables().find((t) => t.table === this.scrapeTable()) ?? null,
  );

  /**
   * Every season this table knows about, newest first, with the season being played
   * folded in whether or not the scraper's list reaches it — which for two of the three
   * it does not, and that is the point.
   */
  readonly seasonOptions = computed(() => {
    const known = this.selectedScrapable()?.default_seasons ?? [];
    const now = this.currentSeason();
    return [...new Set(now ? [now, ...known] : known)].sort().reverse();
  });

  /**
   * Whether a bare `fantabot db scrape <table>` in a terminal would miss the season being
   * played. Rendered as a note about the *command*, not about this form: the form has
   * already defaulted away from it, and the operator with a terminal open has not.
   */
  readonly scrapeDefaultIsStale = computed(
    () => this.selectedScrapable()?.default_is_stale ?? false,
  );

  /** Whether what is about to run leaves out the season being played. */
  readonly scrapeMissesCurrentSeason = computed(() => {
    const now = this.currentSeason();
    return now !== '' && this.scrapeSeasons().length > 0 && !this.scrapeSeasons().includes(now);
  });

  readonly canScrape = computed(
    () => !this.scrapeRunning() && this.scrapeTable() !== '' && this.scrapeSeasons().length > 0,
  );

  /**
   * Presentation only — the registry's `status` and `ok` are unchanged, this just names
   * the four states a reader cares about. `ok === false` on a terminal job is the partial
   * one; `status === 'error'` is the job itself having failed.
   */
  readonly outcome = computed<SyncOutcome>(() => {
    const status = this.jobStatus();
    if (!status) return 'idle';
    if (status === 'running') return 'running';
    if (status === 'error') return 'failed';
    return this.jobOk() === false ? 'partial' : 'complete';
  });

  readonly statusLabel = computed(() => OUTCOME_LABEL[this.outcome()]);
  readonly statusIcon = computed(() => OUTCOME_ICON[this.outcome()]);

  constructor() {
    this.reattach();
    this.readScrapeTables();
  }

  /**
   * What may be scraped, and which season is being played.
   *
   * The season comes from the server rather than from a `new Date()` here: a browser in
   * another timezone would disagree with the server about which season is current, and
   * the disagreement would surface as a form default nobody chose.
   *
   * A picker that cannot be read leaves the card unusable and the rest of the page
   * working, which is what it is — not an error banner about a request the operator
   * did not make.
   */
  private readScrapeTables(): void {
    this.scrape
      .tables()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((found) => {
        this.scrapeTables.set(found.tables);
        this.currentSeason.set(found.current_season);
        if (this.scrapeSeasons().length === 0) this.scrapeSeasons.set([found.current_season]);
      });
  }

  /**
   * Choose a table. The seasons are **not** re-derived from its default list.
   *
   * That is the whole of T23 on this page: `voti` and `statistiche` default to a list
   * that stops before the season being played, so a form that inherited it would
   * reproduce the terminal's trap — a run that scrapes last season and reports success.
   */
  setScrapeTable(table: string): void {
    this.scrapeTable.set(table);
  }

  setScrapeSeasons(seasons: string[]): void {
    this.scrapeSeasons.set(seasons);
  }

  /** Start the supervised child — `fantabot db scrape <table> --season ...`. */
  runScrape(): void {
    if (!this.canScrape()) return;
    this.scrapeError.set(null);
    this.scrapeLines.set([]);
    this.scrapeOk.set(null);
    this.scrapeStatus.set('running');
    this.scrape
      .run({ table: this.scrapeTable(), seasons: this.scrapeSeasons() })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (started) => {
          this.scrapeRunning.set(true);
          this.scrapeJobId.set(started.job_id);
          this.poll(this.scrapePanel, started.job_id);
        },
        error: (failure: { error?: { detail?: string } }) => {
          this.scrapeStatus.set('');
          // The route's own sentence when it has one: a refused season is a line the
          // operator has to rewrite, and the command prints exactly this.
          this.scrapeError.set(failure.error?.detail ?? 'Could not start the scrape.');
        },
      });
  }

  /**
   * Ask the child to stop. Safe in a way `harvest collect` is not: every write is an
   * upsert, `voti` commits per giornata and the site is still there, so a stop costs
   * fetch time and never a row.
   */
  stopScrape(): void {
    const jobId = this.scrapeJobId();
    if (!jobId) return;
    this.jobs
      .stop(jobId)
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe();
  }

  setLeagueId(value: string): void {
    const parsed = Number(value);
    this.leagueId.set(Number.isFinite(parsed) && value.trim() !== '' ? parsed : null);
  }

  runLegaSync(): void {
    const id = this.leagueId();
    if (!id || this.running()) return;
    this.startJob(this.actions.runLegaSync(id));
  }

  /**
   * Capture our own team's credits and roster ids — `fantabot db snapshot-team`.
   *
   * Gated on the league id the page already has. The server requires it too, but the
   * button is the first place to refuse: `league_team_snapshot` is append-only, so a row
   * written under a lega nobody picked stays there.
   */
  captureSnapshot(): void {
    const id = this.leagueId();
    if (!id || this.snapshotting()) return;
    this.snapshotting.set(true);
    this.snapshot.set(null);
    this.teams
      .snapshot({ league_id: id })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.snapshot.set(result);
          this.snapshotting.set(false);
        },
        error: () => {
          this.snapshot.set(UNREACHABLE_SNAPSHOT(id));
          this.snapshotting.set(false);
        },
      });
  }

  /**
   * Resolve club codes to full names — `fantabot db backfill-teams`.
   *
   * No league id, because the command takes none: `teams` is Serie A's clubs, not a
   * lega's. Gating this on the field would invent a dependency the CLI does not have.
   */
  resolveClubNames(): void {
    if (this.backfilling()) return;
    this.backfilling.set(true);
    this.backfill.set(null);
    this.teams
      .backfill()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.backfill.set(result);
          this.backfilling.set(false);
        },
        error: () => {
          this.backfill.set(UNREACHABLE_BACKFILL);
          this.backfilling.set(false);
        },
      });
  }

  /**
   * Pick up a sync that is still running from an earlier page load.
   *
   * The running job id used to live only here, so a refresh mid-sync orphaned the job
   * invisibly *and* re-enabled the button that would start a second one. The server's
   * listing is the source of truth now.
   *
   * A listing that cannot be read is not an error worth showing: nothing the operator
   * asked for has failed, and a red banner on arrival would be about the poll, not them.
   */
  private reattach(): void {
    this.jobs
      .list()
      .pipe(
        catchError(() => EMPTY),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((list) => {
        const resume = (kind: string, panel: JobPanel) => {
          const live = list.jobs.find((job) => job.kind === kind && job.status === 'running');
          if (!live) return;
          panel.running.set(true);
          panel.status.set('running');
          panel.jobId.set(live.id);
          this.poll(panel, live.id);
        };

        resume(KIND, this.legaPanel);
        // A scrape is a subprocess and outlives this page. Re-enabling the button would
        // start a second child against the same live site, which is the opposite of polite.
        resume(SCRAPE_KIND, this.scrapePanel);
      });
  }

  private startJob(request: Observable<{ job_id: string }>): void {
    this.errorMsg.set(null);
    this.lines.set([]);
    this.jobOk.set(null);
    this.jobStatus.set('running');
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => {
        this.running.set(true);
        this.legaPanel.jobId.set(result.job_id);
        this.poll(this.legaPanel, result.job_id);
      },
      error: () => {
        this.jobStatus.set('');
        this.errorMsg.set('Could not start the job.');
      },
    });
  }

  /**
   * Poll one job, asking only for the lines it has not already shown.
   *
   * `since` starts wherever `lines` already is, so a reattach after a refresh does not
   * re-render the whole log, and a resumed poll appends rather than replaces.
   */
  private poll(panel: JobPanel, jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, panel.lines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) panel.lines.update((shown) => [...shown, ...job.lines]);
        panel.status.set(job.status);
        if (job.status !== 'running') {
          panel.running.set(false);
          panel.ok.set(job.ok);
        }
      });
  }
}
