import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { LucideAngularModule } from 'lucide-angular';
import { Observable, switchMap, takeWhile, timer } from 'rxjs';

import { AuthService } from '../../core/api/auth.service';
import { JobsService } from '../../core/api/jobs.service';
import { AuthStatus } from '../../core/models/auth-status';
import { DisconnectDialogComponent, DisconnectRequest } from './disconnect-dialog';

type Tone = 'ok' | 'warn' | 'bad';
type ConnectKind = 'league' | 'fantalab';

@Component({
  selector: 'app-accounts',
  imports: [
    LucideAngularModule,
    DatePipe,
    MatButtonModule,
    MatCardModule,
    MatDividerModule,
    MatProgressBarModule,
  ],
  templateUrl: './accounts.html',
  styleUrl: './accounts.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountsComponent implements OnInit {
  private readonly service = inject(AuthService);
  private readonly jobs = inject(JobsService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly dialog = inject(MatDialog);

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

  // Disconnect flow. `pendingId` holds the one key armed for removal — one key at a
  // time, so arming a second row disarms the first and two rows can never be primed at
  // once. The confirmation used to be the row itself, because the app had no dialog
  // primitive and `window.confirm` blocks the page; it is now a `MatDialog` opened with
  // `role: 'alertdialog'`, which is the M3 surface for a decision at every size class.
  // `pendingId` survives that change because it is also what makes every OTHER row's
  // Disconnect inert while one is armed.
  readonly pendingId = signal<string | null>(null);
  readonly removingId = signal<string | null>(null);

  /** The one open confirmation, so `disarmDisconnect()` can close it from outside. */
  private confirmRef: MatDialogRef<DisconnectDialogComponent, boolean> | null = null;

  // Where focus goes when a confirmed removal takes the row away. `restoreFocus` would
  // send it back to the Disconnect button, which is disabled by then (`disconnectBusy`)
  // and so cannot take focus — leaving it on <body>, at the top of the document.
  private readonly leaguesHeading = viewChild<ElementRef<HTMLElement>>('leaguesHeading');
  private readonly fantalabHeading = viewChild<ElementRef<HTMLElement>>('fantalabHeading');

  /**
   * Every row's Disconnect is inert while one row is armed or being removed.
   *
   * Two reasons. Armed, the button that armed it stays on screen disabled rather than
   * being swapped for the confirm, so a double-click cannot arm and confirm in one
   * gesture. In flight, arming a second row would be silently undone: the delete's
   * completion clears `pendingId` for whichever row now holds it.
   */
  readonly disconnectBusy = computed(() => this.pendingId() !== null || this.removingId() !== null);

  ngOnInit(): void {
    this.load();
    this.reattach();
  }

  /**
   * Pick up a login that is already running.
   *
   * This page held its job id in a private field and asked only `jobs.get(id)`, so a
   * refresh mid-login **orphaned the job**: the browser window stayed open, the job sat
   * waiting to be told the operator had signed in, and the page that could tell it had
   * forgotten which job it was. `harvest`, `news` and `synchronize` all reattach from
   * `jobs.list()`; this is that, and it matters more here than on any of them — this is the
   * one page where a job parks *awaiting a human*, so an orphan waits for ever.
   *
   * A listing that cannot be read is not an error worth showing: nothing the operator asked
   * for has failed, and a red banner on arrival would be about the poll, not about them.
   */
  private reattach(): void {
    this.jobs.running(this.destroyRef).subscribe((jobs) => {
      const live = jobs.find((job) => job.kind === 'auth-login' || job.kind === 'fantalab-login');
      if (!live) return;
      this.jobId = live.id;
      this.connectKind.set(live.kind === 'fantalab-login' ? 'fantalab' : 'league');
      this.pollUntilDone();
    });
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

  /**
   * Arm one row and ask about it.
   *
   * `label` is the credential as the operator knows it and is only ever passed by the
   * template; called without one — which the specs do, to assert the guard below — the
   * key stands in, because no caller without a row has a better name to offer.
   */
  armDisconnect(key: string, label?: string | number): void {
    if (this.disconnectBusy()) return; // the template disables this; belt and braces
    this.connectError.set(null);
    this.pendingId.set(key);
    this.askToDisconnect(key, String(label ?? key));
  }

  disarmDisconnect(): void {
    this.pendingId.set(null);
    const open = this.confirmRef;
    this.confirmRef = null;
    open?.close(false);
  }

  disconnectLeague(leagueId: number): void {
    this.runDisconnect(this.rowKey('league', leagueId), this.service.forgetLeague(leagueId));
  }

  disconnectFantalab(userId: string): void {
    this.runDisconnect(this.rowKey('fantalab', userId), this.service.forgetFantalab(userId));
  }

  tone(state: string): Tone {
    if (state.startsWith('ok')) return 'ok';
    if (state.startsWith('ORPHANED')) return 'warn';
    return 'bad'; // EXPIRED, KEY MISMATCH, MISSING
  }

  /** `rowKey`'s inverse: the one thing the armed key still has to be read back for. */
  private askToDisconnect(key: string, name: string): void {
    const separator = key.indexOf(':');
    const data: DisconnectRequest = {
      kind: key.slice(0, separator) === 'fantalab' ? 'fantalab' : 'league',
      id: key.slice(separator + 1),
      name,
    };

    const ref = this.dialog.open<DisconnectDialogComponent, DisconnectRequest, boolean>(
      DisconnectDialogComponent,
      {
        // An alertdialog is announced whole, so the operator hears what is lost and not
        // only the title. `autoFocus` is Material's default and is safe because Keep is
        // first in the dialog's DOM.
        role: 'alertdialog',
        ariaDescribedBy: 'disconnect-dialog-body',
        // M3 sizes a simple dialog to 560dp. `maxWidth` has to move with it: Material's
        // own default is 80vw, which on a 360px phone would leave 36px of gutter either
        // side instead of the 16px the compact size class asks for.
        width: 'min(560px, calc(100vw - 32px))',
        maxWidth: 'calc(100vw - 32px)',
        data,
      },
    );
    this.confirmRef = ref;

    ref
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((confirmed) => {
        this.confirmRef = null;
        if (!confirmed) {
          // Focus is already back on the Disconnect button: `restoreFocus` is on, and
          // nothing has disabled it, because nothing was removed.
          this.pendingId.set(null);
          return;
        }
        if (data.kind === 'league') this.disconnectLeague(Number(data.id));
        else this.disconnectFantalab(data.id);
      });
  }

  /**
   * Put focus on the heading of the section the removed row was in.
   *
   * `restoreFocus` sends focus back to the Disconnect button that opened the dialog, and
   * on a confirm that button is disabled by the time the overlay detaches — a disabled
   * button cannot take focus, so the restore silently lands on `<body>`. This runs a
   * whole HTTP round-trip after the dialog closed, which is what keeps it out of a race
   * with that restore rather than a step ahead of it by luck.
   */
  private focusSection(key: string): void {
    const heading = key.startsWith('fantalab:') ? this.fantalabHeading() : this.leaguesHeading();
    heading?.nativeElement.focus();
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
        this.focusSection(key);
      },
      error: () => {
        this.pendingId.set(null);
        this.removingId.set(null);
        this.connectError.set('Could not disconnect. Nothing was removed.');
        // The row survives and its button is enabled again, but focus is already gone:
        // the restore ran while it was still disabled. The heading is where to land, and
        // the error banner is a `role="alert"`, so the failure is announced either way.
        this.focusSection(key);
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
            this.connectError.set(job.error ?? 'Sign-in did not complete. Nothing was stored.');
          }
          this.connectKind.set(null);
          this.finishing.set(false);
          this.jobId = null;
          this.load();
        }
      });
  }
}
