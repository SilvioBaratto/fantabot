import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ANIMATION_MODULE_TYPE } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { AccountsComponent } from './accounts';

describe('AccountsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        ICON_PROVIDER,
        // The disconnect confirmation is a MatDialog, and Material closes one behind a
        // real timer unless animations are off. With them off the close resolves on a
        // microtask, so a test can assert the DELETE without waiting out an animation.
        { provide: ANIMATION_MODULE_TYPE, useValue: 'NoopAnimations' },
        {
          provide: LucideIconConfig,
          useFactory: () => {
            const cfg = new LucideIconConfig();
            cfg.size = 16;
            return cfg;
          },
        },
      ],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    // A dialog panel is attached to the document, not to the fixture, so a test that
    // leaves one open would otherwise be visible to the next one's `document` queries.
    // The TestBed teardown removes it too; this makes the guarantee not depend on that.
    document.querySelectorAll('.cdk-overlay-container').forEach((node) => node.remove());
  });

  function flush(body: object) {
    httpMock.expectOne(`${environment.apiUrl}auth/status`).flush(body);
  }

  /**
   * The reattach listing, made once on init (3.1) so a refresh mid-login cannot orphan the
   * job. Only the *render* sites answer it — `load()` re-reads the status alone, so a
   * reload's `flush()` must not expect a second one.
   */
  function flushJobs(jobs: { jobs: unknown[] } = { jobs: [] }) {
    httpMock.expectOne(`${environment.apiUrl}jobs`).flush(jobs);
  }

  const ONE_LEAGUE = {
    has_key: true,
    fantalab: [{ user_id: 'user9', captured_at: '2026-09-01T00:00:00Z', last_used_at: null }],
    leagues: [
      {
        league_id: 4103937,
        league_name: 'Legamiallerotaie2',
        state: 'ok (364d)',
        expires_at: '2027-09-03T00:00:00Z',
        last_verified_at: null,
        user_id: 1,
        team_id: 2,
      },
    ],
  };

  async function rendered(body: object = ONE_LEAGUE) {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush(body);
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  /** Let `timer(0, …)` emit — the first jobs poll is a macrotask away. */
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

  function click(fixture: { nativeElement: HTMLElement }, selector: string) {
    const button = fixture.nativeElement.querySelector(selector) as HTMLButtonElement | null;
    expect(button).toBeTruthy();
    button!.click();
  }

  /**
   * The confirmation is a dialog, and a dialog panel is a CDK overlay attached to the
   * document — not to the fixture. Everything about it is queried from `document`.
   */
  function dialogPanel(): HTMLElement | null {
    return document.querySelector('[role="alertdialog"]');
  }

  function clickInDialog(selector: string) {
    const panel = dialogPanel();
    expect(panel).toBeTruthy();
    const button = panel!.querySelector(selector) as HTMLButtonElement | null;
    expect(button).toBeTruthy();
    button!.click();
  }

  function dialogActionLabels(): string[] {
    const panel = dialogPanel();
    expect(panel).toBeTruthy();
    return Array.from(panel!.querySelectorAll('mat-dialog-actions button')).map((button) =>
      (button.textContent ?? '').trim(),
    );
  }

  it('renders a connected league with its state', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({
      has_key: true,
      fantalab: [{ user_id: 'user9', captured_at: '2026-09-01T00:00:00Z', last_used_at: null }],
      leagues: [
        {
          league_id: 4103937,
          league_name: 'Legamiallerotaie2',
          state: 'ok (364d)',
          expires_at: '2027-09-03T00:00:00Z',
          last_verified_at: null,
          user_id: 1,
          team_id: 2,
        },
      ],
    });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Legamiallerotaie2');
    expect(text).toContain('ok (364d)');
    expect(text).toContain('user9');
  });

  it('shows an empty state when no leagues are connected', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No account connected yet');
  });

  it('warns when no encryption key is set', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: false, fantalab: [], leagues: [] });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('FANTABOT_ENCRYPTION_KEY');
  });

  it('gives the page exactly one h1 and no skipped heading level', async () => {
    const fixture = await rendered();
    const levels = Array.from(
      fixture.nativeElement.querySelectorAll('h1, h2, h3, h4, h5, h6') as NodeListOf<HTMLElement>,
    ).map((heading) => Number(heading.tagName.slice(1)));

    expect(levels.filter((level) => level === 1)).toHaveLength(1);
    expect(levels[0]).toBe(1);
    expect(Math.max(...levels)).toBe(2);
  });

  it('starts a login job and begins watching it with no confirm step', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.onConnect();
    httpMock.expectOne((r) => r.url.includes('auth/login')).flush({ job_id: 'J1' });
    fixture.detectChanges();
    expect(fixture.componentInstance.connecting()).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('Sign in in the browser window');

    // The job is already being watched — this used to require pressing Continue first.
    await tick();
    httpMock
      .expectOne(`${environment.apiUrl}jobs/J1`)
      .flush({ id: 'J1', status: 'running', lines: ['Waiting for you'], ok: null, error: null });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Waiting for you');

    fixture.destroy();
  });

  it('finishes on its own when the job reports the credential was captured', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.onConnect();
    httpMock.expectOne((r) => r.url.includes('auth/login')).flush({ job_id: 'J1' });
    fixture.detectChanges();
    await tick();

    httpMock
      .expectOne(`${environment.apiUrl}jobs/J1`)
      .flush({ id: 'J1', status: 'done', lines: ['2 token(s) stored'], ok: true, error: null });
    fixture.detectChanges();

    expect(fixture.componentInstance.connecting()).toBe(false);
    expect(fixture.componentInstance.captureStatus()).toContain('saved');
    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
  });

  it('confirms the sign-in and keeps watching the same job', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    flushJobs();
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.onConnect();
    httpMock.expectOne((r) => r.url.includes('auth/login')).flush({ job_id: 'J1' });
    fixture.detectChanges();
    await tick();
    httpMock
      .expectOne(`${environment.apiUrl}jobs/J1`)
      .flush({ id: 'J1', status: 'running', lines: [], ok: null, error: null });
    fixture.detectChanges();

    // The confirm is the trigger for the read; polling for it instead opened a burst
    // of tabs over the login form every two seconds.
    click(fixture, '[data-confirm-signin]');
    httpMock.expectOne(`${environment.apiUrl}auth/login/J1/confirm`).flush({ ok: true });
    fixture.detectChanges();

    expect(fixture.componentInstance.finishing()).toBe(true);
    expect(fixture.nativeElement.querySelector('[data-cancel-connect]')).toBeTruthy();
    fixture.destroy();
  });

  // --- disconnect ---

  it('offers a disconnect control on every stored credential', async () => {
    const fixture = await rendered();
    expect(fixture.nativeElement.querySelector('[data-disconnect-league="4103937"]')).toBeTruthy();
    expect(fixture.nativeElement.querySelector('[data-disconnect-fantalab="user9"]')).toBeTruthy();
  });

  it('asks before removing, and sends nothing until the confirm is clicked', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();

    // The prompt names the lega, as `fantabot auth forget` does before its confirm —
    // and says what else goes, because the disconnect now takes the synced data too.
    const prompt = dialogPanel()!.textContent as string;
    expect(prompt).toContain('Disconnect Legamiallerotaie2?');
    expect(prompt).toContain('everything the last sync saved');
    // The arming click alone must not delete anything — afterEach's verify() would
    // fail on an unexpected DELETE, which is precisely the guarantee under test.
    httpMock.expectNone(`${environment.apiUrl}auth/league/4103937`);
  });

  it('names the FantaLab session, and not a lega, in its own confirmation', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-fantalab="user9"]');
    fixture.detectChanges();

    const prompt = dialogPanel()!.textContent as string;
    expect(prompt).toContain('Disconnect user9?');
    expect(prompt).toContain('FantaLab session');
    // Only a lega purge takes the synced data with it; saying so here would be a lie.
    expect(prompt).not.toContain('everything the last sync saved');
  });

  it('deletes the league token and reloads from the server on confirm', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    clickInDialog('[data-confirm-league="4103937"]');
    await tick();

    const request = httpMock.expectOne(`${environment.apiUrl}auth/league/4103937`);
    expect(request.request.method).toBe('DELETE');
    request.flush({ ok: true, removed: true, rows_removed: 1779 });

    // The row is not spliced out locally: the component re-reads status.
    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.nativeElement.textContent).toContain('No account connected yet');
  });

  it('deletes a FantaLab session on confirm', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-fantalab="user9"]');
    fixture.detectChanges();
    clickInDialog('[data-confirm-fantalab="user9"]');
    await tick();

    const request = httpMock.expectOne(`${environment.apiUrl}auth/fantalab/user9`);
    expect(request.request.method).toBe('DELETE');
    request.flush({ ok: true, removed: true, rows_removed: 0 });

    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
  });

  it('disarms without deleting when Keep is clicked', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    expect(fixture.componentInstance.pendingId()).toBe('league:4103937');

    const keep = Array.from(dialogPanel()!.querySelectorAll('mat-dialog-actions button')).find(
      (button) => (button.textContent ?? '').trim() === 'Keep',
    ) as HTMLButtonElement | undefined;
    expect(keep).toBeTruthy();
    keep!.click();
    await tick();
    fixture.detectChanges();

    expect(fixture.componentInstance.pendingId()).toBeNull();
    expect(dialogPanel()).toBeNull();
    httpMock.expectNone(`${environment.apiUrl}auth/league/4103937`);
  });

  it('closes the open confirmation when the page disarms', async () => {
    // `disarmDisconnect()` is the page's own escape hatch. Leaving the dialog up while
    // `pendingId` is null would offer a Disconnect for a row nothing is armed on.
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    expect(dialogPanel()).toBeTruthy();

    fixture.componentInstance.disarmDisconnect();
    await tick();
    fixture.detectChanges();

    expect(fixture.componentInstance.pendingId()).toBeNull();
    expect(dialogPanel()).toBeNull();
    httpMock.expectNone(`${environment.apiUrl}auth/league/4103937`);
  });

  it('says the credential is still stored when the delete fails', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    clickInDialog('[data-confirm-league="4103937"]');
    await tick();

    httpMock
      .expectOne(`${environment.apiUrl}auth/league/4103937`)
      .flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('Nothing was removed');
    // No reload on failure: the displayed row is still the truth.
    httpMock.expectNone(`${environment.apiUrl}auth/status`);
  });

  // Three defects an adversarial review reproduced in a real browser. Each is pinned
  // here so the layout cannot drift back into them.

  it('keeps the arming button on screen but disabled, so a double-click cannot confirm', async () => {
    // The confirm used to REPLACE this button and overlapped its footprint: a
    // double-click armed on the first click and removed on the second.
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();

    const arm = fixture.nativeElement.querySelector(
      '[data-disconnect-league="4103937"]',
    ) as HTMLButtonElement;
    expect(arm).toBeTruthy();
    expect(arm.disabled).toBe(true);
  });

  it('asks in an alertdialog whose actions put Keep ahead of the destructive button', async () => {
    // Inline, the confirm cluster overflowed the card's overflow-hidden at phone widths
    // and "Keep" became untappable — leaving the destructive button as the only target.
    // In an overlay it cannot be clipped by the row, and DOM order is what decides where
    // `autoFocus: 'first-tabbable'` lands.
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();

    const panel = dialogPanel();
    expect(panel).toBeTruthy();

    const arm = fixture.nativeElement.querySelector('[data-disconnect-league="4103937"]')!;
    expect(panel!.contains(arm)).toBe(false);

    // Both escape routes live in the same actions row, so neither can be clipped alone,
    // and the destructive one is never first.
    expect(dialogActionLabels()).toEqual(['Keep', 'Disconnect']);
    const confirm = panel!.querySelector('[data-confirm-league="4103937"]');
    expect(confirm).toBeTruthy();
    expect(Array.from(panel!.querySelectorAll('mat-dialog-actions button')).indexOf(confirm!)).toBe(
      1,
    );
  });

  it('moves focus to the section heading when the confirmed row goes away', async () => {
    // `restoreFocus` would send focus back to the Disconnect button, which this removal
    // has just disabled — so it would land nowhere, at the top of the document.
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    clickInDialog('[data-confirm-league="4103937"]');
    await tick();

    httpMock
      .expectOne(`${environment.apiUrl}auth/league/4103937`)
      .flush({ ok: true, removed: true, rows_removed: 1 });

    expect((document.activeElement as HTMLElement | null)?.id).toBe('leagues-heading');

    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
  });

  it('cannot arm a second row while a delete is in flight', async () => {
    const fixture = await rendered({
      has_key: true,
      fantalab: [],
      leagues: [
        {
          league_id: 111,
          league_name: 'A',
          state: 'ok (1d)',
          expires_at: '2027-01-01T00:00:00Z',
          last_verified_at: null,
          user_id: 1,
          team_id: 1,
        },
        {
          league_id: 222,
          league_name: 'B',
          state: 'ok (1d)',
          expires_at: '2027-01-01T00:00:00Z',
          last_verified_at: null,
          user_id: 1,
          team_id: 2,
        },
      ],
    });

    click(fixture, '[data-disconnect-league="111"]');
    fixture.detectChanges();
    clickInDialog('[data-confirm-league="111"]');
    await tick();
    fixture.detectChanges();

    // 111's DELETE is open. B's button must be inert — arming it would be wiped when
    // 111 lands, because completion clears whatever pendingId then holds.
    const other = fixture.nativeElement.querySelector(
      '[data-disconnect-league="222"]',
    ) as HTMLButtonElement;
    expect(other.disabled).toBe(true);

    fixture.componentInstance.armDisconnect('league:222');
    expect(fixture.componentInstance.pendingId()).toBe('league:111');

    httpMock
      .expectOne(`${environment.apiUrl}auth/league/111`)
      .flush({ ok: true, removed: true, rows_removed: 1 });
    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
  });

  // --- FantaLab connect ---

  it('offers a FantaLab connect button, worded for the empty state', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    const button = fixture.nativeElement.querySelector('[data-connect-fantalab]');
    expect(button).toBeTruthy();
    expect(button.textContent).toContain('Connect FantaLab');
  });

  it('offers to replace an existing FantaLab session', async () => {
    const fixture = await rendered();
    const button = fixture.nativeElement.querySelector('[data-connect-fantalab]');
    expect(button.textContent).toContain('Reconnect FantaLab');
  });

  it('starts a FantaLab login job and names the right site in the panel', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    click(fixture, '[data-connect-fantalab]');

    const request = httpMock.expectOne((r) => r.url.includes('auth/fantalab-login'));
    expect(request.request.method).toBe('POST');
    request.flush({ job_id: 'FL1' });
    fixture.detectChanges();

    expect(fixture.componentInstance.connectKind()).toBe('fantalab');
    await tick();
    httpMock
      .expectOne(`${environment.apiUrl}jobs/FL1`)
      .flush({ id: 'FL1', status: 'running', lines: [], ok: null, error: null });
    fixture.detectChanges();
    // The shared panel must not tell a FantaLab user to sign in to fantacalcio.it.
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Sign in to FantaLab');
    expect(text).not.toContain('Sign in to fantacalcio.it');
    fixture.destroy();
  });

  it('forces the FantaLab re-auth when a session is already stored', async () => {
    // The backend opens no browser while any session exists, so without force an
    // existing session could never be replaced.
    const fixture = await rendered();
    click(fixture, '[data-connect-fantalab]');

    const request = httpMock.expectOne((r) => r.url.includes('auth/fantalab-login'));
    expect(request.request.urlWithParams).toContain('force=true');
    request.flush({ job_id: 'FL1' });
    fixture.destroy();
  });

  it('does not force the FantaLab capture when nothing is stored', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    click(fixture, '[data-connect-fantalab]');

    const request = httpMock.expectOne((r) => r.url.includes('auth/fantalab-login'));
    expect(request.request.urlWithParams).toContain('force=false');
    request.flush({ job_id: 'FL1' });
    fixture.destroy();
  });

  it('watches a FantaLab job without any confirm step', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    click(fixture, '[data-connect-fantalab]');
    httpMock.expectOne((r) => r.url.includes('auth/fantalab-login')).flush({ job_id: 'FL1' });
    fixture.detectChanges();
    await tick();

    httpMock
      .expectOne(`${environment.apiUrl}jobs/FL1`)
      .flush({ id: 'FL1', status: 'running', lines: [], ok: null, error: null });
    fixture.destroy();
  });

  it('locks both connect buttons while a capture is running', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    click(fixture, '[data-connect-fantalab]');
    httpMock.expectOne((r) => r.url.includes('auth/fantalab-login')).flush({ job_id: 'FL1' });
    fixture.detectChanges();

    const fantalab = fixture.nativeElement.querySelector(
      '[data-connect-fantalab]',
    ) as HTMLButtonElement;
    expect(fantalab.disabled).toBe(true);
    fixture.destroy();
  });

  it('forces the re-auth while another token is still stored', async () => {
    // Without force the backend opens no browser when every remaining token is valid,
    // so a lega disconnected from a multi-lega account could never be reconnected here.
    const fixture = await rendered();
    fixture.componentInstance.onConnect();

    const request = httpMock.expectOne((r) => r.url.includes('auth/login'));
    expect(request.request.urlWithParams).toContain('force=true');
    request.flush({ job_id: 'J1' });
    fixture.destroy();
  });

  it('does not force when nothing is stored', async () => {
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    fixture.componentInstance.onConnect();

    const request = httpMock.expectOne((r) => r.url.includes('auth/login'));
    expect(request.request.urlWithParams).toContain('force=false');
    request.flush({ job_id: 'J1' });
    fixture.destroy();
  });

  it('re-enables Continue when the job asks again', async () => {
    // Confirming before the browser has written the credential is an ordinary mistake.
    // It used to spend a minute re-reading and then kill the login; now the flow asks
    // again, and the button has to come back or there is no way to answer.
    //
    // The clock is faked from before the poll is created, because the second poll is
    // 1500ms out and an already-scheduled interval ignores a later useFakeTimers().
    const fixture = await rendered({ has_key: true, fantalab: [], leagues: [] });
    vi.useFakeTimers();
    try {
      fixture.componentInstance.onConnect();
      httpMock.expectOne((r) => r.url.includes('auth/login')).flush({ job_id: 'J1' });
      fixture.detectChanges();

      await vi.advanceTimersByTimeAsync(1);
      httpMock.expectOne(`${environment.apiUrl}jobs/J1`).flush({
        id: 'J1',
        status: 'running',
        lines: [],
        ok: null,
        error: null,
        awaiting_confirm: true,
      });
      fixture.detectChanges();

      click(fixture, '[data-confirm-signin]');
      httpMock.expectOne(`${environment.apiUrl}auth/login/J1/confirm`).flush({ ok: true });
      fixture.detectChanges();
      expect(fixture.componentInstance.finishing()).toBe(true);

      // The read found nothing, so the job parked on another confirmation.
      await vi.advanceTimersByTimeAsync(1600);
      httpMock.expectOne(`${environment.apiUrl}jobs/J1`).flush({
        id: 'J1',
        status: 'running',
        lines: ['Not signed in yet — the browser has not written your leghe.'],
        ok: null,
        error: null,
        awaiting_confirm: true,
      });
      fixture.detectChanges();

      expect(fixture.componentInstance.finishing()).toBe(false);
      const button = fixture.nativeElement.querySelector(
        '[data-confirm-signin]',
      ) as HTMLButtonElement;
      expect(button.disabled).toBe(false);
      expect(fixture.nativeElement.textContent).toContain('Not signed in yet');
    } finally {
      fixture.destroy();
      vi.useRealTimers();
    }
  });

  it('picks up a login already running, so a refresh does not orphan it', async () => {
    // 3.1's named counter-example. This page held its job id in a private field and asked
    // only `jobs.get(id)`, so a refresh mid-login left the browser window open, the job
    // waiting to be told the operator had signed in, and the page that could tell it
    // having forgotten which job it was. It is the one page where a job parks *awaiting a
    // human*, so an orphan waits for ever.
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush(ONE_LEAGUE);
    flushJobs({
      jobs: [{ id: 'J7', kind: 'auth-login', status: 'running', started_at: '', lines: [] }],
    });
    fixture.detectChanges();
    await tick();

    // It resumed polling *that* job — the id it never knew it had.
    const poll = httpMock.expectOne((r) => r.url.includes('jobs/J7'));
    poll.flush({
      id: 'J7',
      kind: 'auth-login',
      status: 'running',
      lines: ['waiting'],
      awaiting_confirm: true,
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('waiting');
  });

  it('does not adopt a job that belongs to another page', async () => {
    // `harvest-collect` is a running job too, and adopting it would put a harvest log under
    // a Sign in panel and offer a Continue button that means nothing.
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush(ONE_LEAGUE);
    flushJobs({
      jobs: [{ id: 'H1', kind: 'harvest-collect', status: 'running', started_at: '', lines: [] }],
    });
    fixture.detectChanges();
    await tick();

    httpMock.expectNone((r) => r.url.includes('jobs/H1'));
  });

  it('does not adopt a login that has already finished', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush(ONE_LEAGUE);
    flushJobs({
      jobs: [{ id: 'J6', kind: 'auth-login', status: 'done', started_at: '', lines: [] }],
    });
    fixture.detectChanges();
    await tick();

    httpMock.expectNone((r) => r.url.includes('jobs/J6'));
  });
});
