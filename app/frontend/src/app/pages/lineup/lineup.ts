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
import { MatListModule } from '@angular/material/list';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { LucideAngularModule } from 'lucide-angular';

import { LegaService } from '../../core/api/lega.service';
import { LineupService } from '../../core/api/lineup.service';
import { LegaOverview } from '../../core/models/lega';
import { LineupPlan, LineupRuns, SubmitResult } from '../../core/models/lineup';

@Component({
  selector: 'app-lineup',
  imports: [
    LucideAngularModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatCardModule,
    MatListModule,
    MatProgressBarModule,
  ],
  templateUrl: './lineup.html',
  styleUrl: './lineup.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LineupComponent implements OnInit {
  private readonly lega = inject(LegaService);
  private readonly lineup = inject(LineupService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);

  /** Where focus goes when a submit lands — see `focusResult`. */
  private readonly resultBlock = viewChild<ElementRef<HTMLElement>>('resultBlock');

  readonly leagues = signal<LegaOverview[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly plan = signal<LineupPlan | null>(null);
  readonly loading = signal(true);
  readonly planLoading = signal(false);
  readonly errorMsg = signal<string | null>(null);

  /**
   * The dry run's result, and the gate on arming.
   *
   * **Arming requires a second, explicit act on a dry run the operator has just seen.** Not
   * a checkbox — a checkbox pre-ticked from last time is not a second act, and a page can
   * be restored by a session manager with its form state intact. Selecting another lega
   * clears it, so the run on screen is always the run that would be armed.
   */
  readonly dryRun = signal<SubmitResult | null>(null);
  readonly submitting = signal(false);
  readonly result = signal<SubmitResult | null>(null);

  /**
   * Whether a dry run for the *currently selected* lega is on screen — and so whether the
   * second act is offered at all.
   *
   * Deliberately says nothing about `submitting`. The arm button used to live inside
   * `canArm`, which meant clicking it destroyed it: `arm()` sets `submitting` as its first
   * act, the block stopped matching, and the focused element was removed from under the
   * keyboard on the one irreversible action in the app. The button now stays mounted and
   * goes disabled instead.
   */
  readonly armVisible = computed(() => this.dryRun()?.outcome === 'not_armed');

  /** True only while a dry run is on screen and nothing is in flight. */
  readonly canArm = computed(() => this.armVisible() && !this.submitting());

  /**
   * The scheduled job's history — what `launchd` did, read back. Read-only on purpose: the
   * operator turns the job on and off in `.env` and the plist, never here, so there is no
   * control on this page to go with it.
   */
  readonly runs = signal<LineupRuns | null>(null);
  readonly runsError = signal<string | null>(null);

  ngOnInit(): void {
    this.loadLeagues();
    this.loadRuns();
  }

  loadRuns(): void {
    this.runsError.set(null);
    this.lineup
      .getRuns()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (runs) => this.runs.set(runs),
        error: () => this.runsError.set('Could not reach the API for the scheduled history.'),
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

  select(leagueId: number): void {
    this.selectedId.set(leagueId);
    this.plan.set(null);
    // A dry run belongs to one lega. Carrying it across would let an operator arm a lineup
    // they never saw — the whole property this gate exists for.
    this.dryRun.set(null);
    this.result.set(null);
    this.fetchPlan(leagueId);
  }

  /**
   * Re-read the plan without clearing what is on screen.
   *
   * Split from `select` because arming reloads the plan and **must not** wipe the result
   * the operator has just been handed — the read-back is the evidence that the platform
   * kept it, and it was being erased a frame after it arrived.
   */
  private fetchPlan(leagueId: number, { quiet = false } = {}): void {
    // `quiet` skips the loading state. The template swaps the whole panel for a skeleton
    // while `planLoading` is set, so a background refresh after arming would blank the
    // read-back a frame after the operator was handed it — the one thing on that screen
    // that is evidence.
    if (!quiet) this.planLoading.set(true);
    this.lineup
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

  /**
   * Ask what would be sent. Never arms — `arm: false` is passed explicitly, every time.
   */
  runDry(): void {
    const leagueId = this.selectedId();
    if (leagueId === null || this.submitting()) return;
    this.submitting.set(true);
    this.result.set(null);
    this.lineup
      .submit(leagueId, false)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (outcome) => {
          this.dryRun.set(outcome);
          this.submitting.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.submitting.set(false);
        },
      });
  }

  /**
   * Submit for real — the second act.
   *
   * Refuses unless a dry run for this lega is on screen. That is enforced here and not only
   * by hiding the button: a disabled control is a suggestion, and this is the one call in
   * the app that spends a matchday.
   */
  arm(): void {
    const leagueId = this.selectedId();
    if (leagueId === null || !this.canArm()) return;
    this.submitting.set(true);
    this.lineup
      .submit(leagueId, true)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (outcome) => {
          this.result.set(outcome);
          // Spent. The next arm needs its own dry run, on whatever the roster is now.
          this.dryRun.set(null);
          this.submitting.set(false);
          this.fetchPlan(leagueId, { quiet: true });
          this.focusResult();
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.submitting.set(false);
        },
      });
  }

  /**
   * Land focus on the outcome the submit produced.
   *
   * The arm button is disabled while the call is in flight and the whole block goes away
   * once the dry run is spent, so by the time the result is on screen there is nothing
   * left for focus to sit on — the browser drops it on `<body>` and the keyboard restarts
   * at the top of the page, on the one action that cannot be taken back.
   * `afterNextRender`, not a direct call: `result.set` is what *creates* the element being
   * focused, so it does not exist until the view has been refreshed. Same idea as
   * `accounts.ts::focusSection`, which can focus directly only because its heading was
   * already in the DOM.
   */
  private focusResult(): void {
    afterNextRender(() => this.resultBlock()?.nativeElement.focus(), { injector: this.injector });
  }
}
