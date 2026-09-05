import { DatePipe } from '@angular/common';
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
import { Observable, switchMap, takeWhile, timer } from 'rxjs';

import { AuthService } from '../../core/api/auth.service';
import { JobsService } from '../../core/api/jobs.service';
import { AuthStatus } from '../../core/models/auth-status';

type Tone = 'ok' | 'warn' | 'bad';
type ConnectKind = 'league' | 'fantalab';

@Component({
  selector: 'app-accounts',
  imports: [LucideAngularModule, DatePipe],
  templateUrl: './accounts.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class AccountsComponent implements OnInit {
  private readonly service = inject(AuthService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);

  readonly status = signal<AuthStatus | null>(null);
  readonly loading = signal(true);
  readonly errorMsg = signal<string | null>(null);

  // Connect-account flow. Both credentials use it: the job, the prompt gate and the
  // confirm endpoint are shared, and only the copy and the start call differ — so the
  // flow tracks WHICH capture is running rather than duplicating four signals.
  readonly connectKind = signal<ConnectKind | null>(null);
  readonly connecting = computed(() => this.connectKind() !== null);
  readonly connectError = signal<string | null>(null);
  readonly captureLog = signal<string[]>([]);
  readonly captureStatus = signal('');
  readonly finishing = signal(false);
  private jobId: string | null = null;

  // Disconnect flow. The app has no dialog primitive and `window.confirm` blocks the
  // page, so the confirmation is the row itself: `pendingId` holds the one key armed
  // for removal, and the row swaps its button for "Remove / Keep". One key at a time —
  // arming a second row disarms the first, so two rows can never be primed at once.
  readonly pendingId = signal<string | null>(null);
  readonly removingId = signal<string | null>(null);

  /**
   * Every row's Disconnect is inert while one row is armed or being removed.
   *
   * Two reasons. Armed, the button that armed it stays on screen disabled rather than
   * being swapped for the confirm, so a double-click cannot arm and confirm in one
   * gesture. In flight, arming a second row would be silently undone: the delete's
   * completion clears `pendingId` for whichever row now holds it.
   */
  readonly disconnectBusy = computed(
    () => this.pendingId() !== null || this.removingId() !== null,
  );

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errorMsg.set(null);
    this.service
      .getStatus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (status) => {
          this.status.set(status);
          this.loading.set(false);
        },
        error: () => {
          this.errorMsg.set('Could not reach the API.');
          this.loading.set(false);
        },
      });
  }

  onConnect(): void {
    // Force whenever a league token survives: the backend opens no browser when every
    // remaining token is valid, so re-connecting a lega that was just disconnected
    // would otherwise report success and store nothing.
    const force = (this.status()?.leagues.length ?? 0) > 0;
    this.startCapture('league', this.service.startLogin(0, force));
  }

  onConnectFantalab(): void {
    // Same trap, stricter: the backend refuses to open a browser while ANY session is
    // stored, so without force an existing session could never be replaced.
    const force = (this.status()?.fantalab.length ?? 0) > 0;
    this.startCapture('fantalab', this.service.startFantalabLogin(force));
  }

  private startCapture(kind: ConnectKind, request: Observable<{ job_id: string }>): void {
    this.connectError.set(null);
    this.captureLog.set([]);
    this.captureStatus.set('');
    this.finishing.set(false);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => {
        this.jobId = result.job_id;
        this.connectKind.set(kind);
        // Start watching immediately. This used to hang off the Continue button, so
        // with the button gone nothing would ever begin polling and the panel would
        // sit there for ever on an otherwise successful login.
        this.pollUntilDone();
      },
      error: () => this.connectError.set('Could not start login.'),
    });
  }

  /**
   * Tell the job you have signed in, so it reads the credential.
   *
   * Detecting this automatically was built and reverted: polling the browser's
   * storage opened a burst of tabs over the login form every two seconds, because
   * the read navigates a temporary page to every origin the site has visited and
   * the ad iframes leave theirs behind.
   */
  onContinue(): void {
    if (!this.jobId) return;
    this.finishing.set(true);
    this.service
      .confirmLogin(this.jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        error: () => {
          this.finishing.set(false);
          this.connectError.set('Could not confirm login.');
        },
      });
  }

  cancelConnect(): void {
    // Stops watching; the job itself runs on until it captures, you close the browser,
    // or its ten-minute deadline passes. Nothing is stored by cancelling.
    this.connectKind.set(null);
    this.finishing.set(false);
    this.jobId = null;
  }

  /** Key for one removable row. Leagues and FantaLab share `pendingId`. */
  rowKey(kind: 'league' | 'fantalab', id: number | string): string {
    return `${kind}:${id}`;
  }

  armDisconnect(key: string): void {
    if (this.disconnectBusy()) return; // the template disables this; belt and braces
    this.connectError.set(null);
    this.pendingId.set(key);
  }

  disarmDisconnect(): void {
    this.pendingId.set(null);
  }

  disconnectLeague(leagueId: number): void {
    this.runDisconnect(
      this.rowKey('league', leagueId),
      this.service.forgetLeague(leagueId),
    );
  }

  disconnectFantalab(userId: string): void {
    this.runDisconnect(
      this.rowKey('fantalab', userId),
      this.service.forgetFantalab(userId),
    );
  }

  tone(state: string): Tone {
    if (state.startsWith('ok')) return 'ok';
    if (state.startsWith('ORPHANED')) return 'warn';
    return 'bad'; // EXPIRED, KEY MISMATCH, MISSING
  }

  private runDisconnect(key: string, request: Observable<{ removed: boolean }>): void {
    this.removingId.set(key);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      // `load()` rather than splicing the row out locally: the server decides what is
      // stored, and a removed=false (nothing there) must redraw from truth, not from
      // an assumption that the delete did something.
      next: () => {
        this.pendingId.set(null);
        this.removingId.set(null);
        this.load();
      },
      error: () => {
        this.pendingId.set(null);
        this.removingId.set(null);
        this.connectError.set('Could not disconnect. Nothing was removed.');
      },
    });
  }

  private pollUntilDone(): void {
    const id = this.jobId;
    if (!id) return;
    // `timer(0, …)`, not `interval(…)`: interval suppresses its first emission, so the
    // panel would show nothing at all for the first 1.5s of a wait that is now the
    // whole interaction.
    timer(0, 1500)
      .pipe(
        switchMap(() => this.jobs.get(id)),
        takeWhile((job) => job.status === 'running', true),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((job) => {
        this.captureLog.set(job.lines ?? []);
        // A login may ask more than once: confirming before the browser has written
        // the credential is an ordinary mistake, answered with another prompt rather
        // than a failure. So the button comes back whenever the job is asking again.
        if (job.awaiting_confirm) this.finishing.set(false);
        if (job.status !== 'running') {
          this.captureStatus.set(
            job.status === 'error'
              ? 'Sign-in did not complete. Nothing was stored.'
              : 'Sign-in detected. The credential was saved.',
          );
          if (job.status === 'error') {
            this.connectError.set(
              job.error ?? 'Sign-in did not complete. Nothing was stored.',
            );
          }
          this.connectKind.set(null);
          this.finishing.set(false);
          this.jobId = null;
          this.load();
        }
      });
  }
}
