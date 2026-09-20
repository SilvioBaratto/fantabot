import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
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
import { LucideAngularModule } from 'lucide-angular';
import { EMPTY, Observable, catchError, interval, switchMap, takeWhile } from 'rxjs';

import { ActionsService } from '../../core/api/actions.service';
import { JobsService } from '../../core/api/jobs.service';
import { TeamsService } from '../../core/api/teams.service';
import { BackfillResult, TeamSnapshotResult } from '../../core/models/teams';
import { IconName } from '../../icons';

/** The job kind this page owns. `GET /jobs` lists every kind; only one belongs here. */
const KIND = 'lega-sync';

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
  ],
  templateUrl: './synchronize.html',
  styleUrl: './synchronize.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SynchronizeComponent {
  private readonly actions = inject(ActionsService);
  private readonly jobs = inject(JobsService);
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
        const live = list.jobs.find((job) => job.kind === KIND && job.status === 'running');
        if (!live) return;
        this.running.set(true);
        this.jobStatus.set('running');
        this.poll(live.id);
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
        this.poll(result.job_id);
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
  private poll(jobId: string): void {
    interval(1500)
      .pipe(
        switchMap(() => this.jobs.get(jobId, this.lines().length)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        if (job.lines.length) this.lines.update((shown) => [...shown, ...job.lines]);
        this.jobStatus.set(job.status);
        if (job.status !== 'running') {
          this.running.set(false);
          this.jobOk.set(job.ok);
        }
      });
  }
}
