import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
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

  afterEach(() => httpMock.verify());

  function flush(body: object) {
    httpMock.expectOne(`${environment.apiUrl}auth/status`).flush(body);
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
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No account connected yet');
  });

  it('warns when no encryption key is set', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: false, fantalab: [], leagues: [] });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('FANTABOT_ENCRYPTION_KEY');
  });

  it('starts a login job and begins watching it with no confirm step', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
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
    expect(
      fixture.nativeElement.querySelector('[data-disconnect-league="4103937"]'),
    ).toBeTruthy();
    expect(
      fixture.nativeElement.querySelector('[data-disconnect-fantalab="user9"]'),
    ).toBeTruthy();
  });

  it('asks before removing, and sends nothing until the confirm is clicked', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();

    // The prompt names the lega, as `fantabot auth forget` does before its confirm —
    // and says what else goes, because the disconnect now takes the synced data too.
    const prompt = fixture.nativeElement.textContent as string;
    expect(prompt).toContain('Disconnect Legamiallerotaie2?');
    expect(prompt).toContain('everything the last sync saved');
    // The arming click alone must not delete anything — afterEach's verify() would
    // fail on an unexpected DELETE, which is precisely the guarantee under test.
    httpMock.expectNone(`${environment.apiUrl}auth/league/4103937`);
  });

  it('deletes the league token and reloads from the server on confirm', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    click(fixture, '[data-confirm-league="4103937"]');

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
    click(fixture, '[data-confirm-fantalab="user9"]');

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

    fixture.componentInstance.disarmDisconnect();
    fixture.detectChanges();
    expect(fixture.componentInstance.pendingId()).toBeNull();
    httpMock.expectNone(`${environment.apiUrl}auth/league/4103937`);
  });

  it('says the credential is still stored when the delete fails', async () => {
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();
    click(fixture, '[data-confirm-league="4103937"]');

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

  it('renders the confirm outside the row cluster, on its own line', async () => {
    // Inline, the cluster overflowed the card's overflow-hidden at phone widths and
    // "Keep" became untappable — leaving the destructive button as the only target.
    const fixture = await rendered();
    click(fixture, '[data-disconnect-league="4103937"]');
    fixture.detectChanges();

    const confirm = fixture.nativeElement.querySelector('[data-confirm-league="4103937"]')!;
    const arm = fixture.nativeElement.querySelector('[data-disconnect-league="4103937"]')!;
    const group = confirm.closest('[role="group"]');
    expect(group).toBeTruthy();
    expect(group!.contains(arm)).toBe(false);
    // Both escape routes live in the same group, so neither can be clipped alone.
    expect(group!.textContent).toContain('Keep');
  });

  it('cannot arm a second row while a delete is in flight', async () => {
    const fixture = await rendered({
      has_key: true,
      fantalab: [],
      leagues: [
        { league_id: 111, league_name: 'A', state: 'ok (1d)', expires_at: '2027-01-01T00:00:00Z', last_verified_at: null, user_id: 1, team_id: 1 },
        { league_id: 222, league_name: 'B', state: 'ok (1d)', expires_at: '2027-01-01T00:00:00Z', last_verified_at: null, user_id: 1, team_id: 2 },
      ],
    });

    click(fixture, '[data-disconnect-league="111"]');
    fixture.detectChanges();
    click(fixture, '[data-confirm-league="111"]');
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
        id: 'J1', status: 'running', lines: [], ok: null, error: null,
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
});
