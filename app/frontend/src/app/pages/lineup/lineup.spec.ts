import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { LineupRun, LineupRuns } from '../../core/models/lineup';
import { LineupComponent } from './lineup';

/** What `GET /lineup/runs` answers when the CLI has not recorded a scheduled run yet. */
const NO_RUNS: LineupRuns = {
  ok: true,
  path: '/Users/x/.fantabot/lineup_runs.jsonl',
  exists: false,
  total: 0,
  skipped: 0,
  runs: [],
  error: null,
  last_at: null,
  last_age_hours: null,
  stale: false,
};

describe('LineupComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LineupComponent],
      providers: [provideHttpClient(), provideHttpClientTesting(), ICON_PROVIDER],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    // The scheduled history loads on init in every test. Answered here so each test states
    // only what it is about — flushed, not ignored: `verify()` still fails on anything else.
    httpMock.match((r) => r.url.includes('lineup/runs')).forEach((r) => r.flush(NO_RUNS));
    httpMock.verify();
  });

  /** A plan the page draws as a refusal — enough to satisfy a `lineup/plan` request. */
  const NO_PLAN = {
    found: false,
    outcome: 'no_lineup',
    reason: 'x',
    module: '',
    matchday: null,
    starters: [],
    bench: [],
  };

  function overview(id: number) {
    return {
      league_id: id,
      league_name: 'Legamiallerotaie2',
      captured_at: null,
      matchday: null,
      budget: 500,
      roster_size: 30,
      min_roles: null,
      max_roles: null,
      modules: null,
      bench_size: null,
      team_count: 8,
    };
  }

  it('renders the planned formation for the first lega', async () => {
    const fixture = TestBed.createComponent(LineupComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock
      .expectOne((r) => r.url.includes('lineup/plan'))
      .flush({
        found: true,
        outcome: 'planned',
        reason: null,
        module: '4-3-3',
        matchday: 3,
        starters: [{ player_id: 1, nome: 'Svilar' }],
        bench: [{ player_id: 2, nome: 'Reserve' }],
      });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('4-3-3');
    expect(text).toContain('Svilar');
  });

  it('titles the page with one h1', async () => {
    // One `h1` per routed page, and the sections under it are all `h2`
    // (`accessibility/designing.md:183`).
    const fixture = TestBed.createComponent(LineupComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
    fixture.detectChanges();
    await fixture.whenStable();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelectorAll('h1').length).toBe(1);
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('Lineup');
  });

  it('offers the leagues as one single-select group', async () => {
    // A segmented button, not chips: `mat-chip-option` deselects on a second click, which
    // here would leave the page with no lega and ask for a plan for `null`.
    const fixture = TestBed.createComponent(LineupComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937), overview(3584692)]);
    fixture.detectChanges();
    await fixture.whenStable();
    httpMock.expectOne((r) => r.url.includes('lineup/plan')).flush(NO_PLAN);
    fixture.detectChanges();
    await fixture.whenStable();

    const el: HTMLElement = fixture.nativeElement;
    const group = el.querySelector('[role="radiogroup"]');
    expect(group?.getAttribute('aria-label')).toBe('Lega');

    const options = group?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [];
    expect(options.length).toBe(2);

    options[1].click();
    fixture.detectChanges();

    expect(fixture.componentInstance.selectedId()).toBe(3584692);
    httpMock.expectOne((r) => r.url.includes('league_id=3584692')).flush(NO_PLAN);
  });

  it('shows the reason when not connected', async () => {
    const fixture = TestBed.createComponent(LineupComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock
      .expectOne((r) => r.url.includes('lineup/plan'))
      .flush({
        found: false,
        outcome: 'no_credential',
        reason: 'No token stored for lega 4103937 — run `fantabot auth login`.',
        module: '',
        matchday: null,
        starters: [],
        bench: [],
      });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    // The heading is the outcome and the remedy is the reason. Both used to be the one
    // string "Not connected, or no lineup available yet", which is four failures wearing
    // one label — the defect T31 records.
    expect(text).toContain('This lega is not connected');
    expect(text).toContain('auth login');
  });

  it('tells a refusal from an unreachable platform', async () => {
    // `apileague` maps every failure onto `TokenError` — deliberately, because a traceback
    // can render the Authorization header — so a bare catch reported timeouts as
    // credential problems. A 403 will not resolve by reloading; a timeout might.
    const fixture = TestBed.createComponent(LineupComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock
      .expectOne((r) => r.url.includes('lineup/plan'))
      .flush({
        found: false,
        outcome: 'refused',
        reason: 'the platform rejected our token for lega 4103937',
        module: '',
        matchday: null,
        starters: [],
        bench: [],
      });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('The platform refused us');
  });

  describe('submitting', () => {
    /**
     * The property: **arming is impossible without seeing the dry run in the same session.**
     * A checkbox pre-ticked from last time is not a second act, and a session manager can
     * restore form state — so the gate is a dry run that is on screen *now*.
     */
    async function withPlan() {
      const fixture = TestBed.createComponent(LineupComponent);
      fixture.detectChanges();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock
        .expectOne((r) => r.url.includes('lineup/plan'))
        .flush({
          found: true,
          outcome: 'planned',
          reason: null,
          module: '4-3-3',
          matchday: 3,
          starters: [{ player_id: 1, nome: 'Svilar' }],
          bench: [],
        });
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    const DRY = {
      outcome: 'not_armed',
      reason: 'the request did not ask to arm',
      module: '4-3-3',
      matchday: 3,
      starters: [{ player_id: 1, nome: 'Svilar' }],
      bench: [],
      submitted: false,
      saved_starters: null,
      saved_at: null,
      rejected: [],
      past_deadline: null,
      unconfirmed: '',
    };

    it('offers no arm control before a dry run', async () => {
      const fixture = await withPlan();

      expect(fixture.componentInstance.canArm()).toBe(false);
      expect(fixture.nativeElement.textContent).not.toContain('Submit this lineup for real');
    });

    it('refuses to arm even if the control is reached anyway', async () => {
      // A hidden button is a suggestion. This is the one call in the app that spends a
      // matchday, so the refusal lives in the method too.
      const fixture = await withPlan();

      fixture.componentInstance.arm();

      httpMock.expectNone((r) => r.url.includes('lineup/submit'));
    });

    it('never asks to arm on a dry run', async () => {
      const fixture = await withPlan();

      fixture.componentInstance.runDry();
      const request = httpMock.expectOne((r) => r.url.includes('lineup/submit'));

      expect(request.request.method).toBe('POST');
      expect(request.request.body).toEqual({ league_id: 4103937, arm: false });
      request.flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('Dry run — nothing was sent');
      expect(fixture.nativeElement.textContent).toContain('4-3-3');
    });

    it('only then offers the second act, and it is the one that arms', async () => {
      const fixture = await withPlan();
      fixture.componentInstance.runDry();
      httpMock.expectOne((r) => r.url.includes('lineup/submit')).flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.canArm()).toBe(true);
      expect(fixture.nativeElement.textContent).toContain('Submit this lineup for real');

      fixture.componentInstance.arm();
      const armed = httpMock.expectOne((r) => r.url.includes('lineup/submit'));
      expect(armed.request.body).toEqual({ league_id: 4103937, arm: true });
      armed.flush({
        ...DRY,
        outcome: 'submitted',
        reason: '',
        submitted: true,
        saved_starters: 11,
      });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('Submitted 4-3-3');
      // Spent: the next arm needs its own dry run, on whatever the roster is now.
      expect(fixture.componentInstance.canArm()).toBe(false);
      httpMock
        .expectOne((r) => r.url.includes('lineup/plan'))
        .flush({
          found: false,
          outcome: 'no_lineup',
          reason: 'x',
          module: '',
          matchday: null,
          starters: [],
          bench: [],
        });
    });

    it('never drops focus on <body> when the irreversible act is taken', async () => {
      // The arm button used to sit inside `@if (canArm())`, and `canArm` included
      // `!submitting()` while `arm()` sets `submitting` as its first act — so clicking the
      // one action in this app that cannot be undone destroyed the focused element and the
      // keyboard restarted at the top of the page. The button now survives the click and
      // the outcome takes focus.
      const fixture = await withPlan();
      fixture.componentInstance.runDry();
      httpMock.expectOne((r) => r.url.includes('lineup/submit')).flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();

      const host = fixture.nativeElement as HTMLElement;
      const armButton = Array.from(host.querySelectorAll('button')).find((b) =>
        b.textContent?.includes('Submit this lineup for real'),
      );
      expect(armButton).toBeDefined();
      armButton?.focus();
      expect(document.activeElement).toBe(armButton);

      armButton?.click();
      fixture.detectChanges();
      await fixture.whenStable();

      // In flight: disabled against a second submit, and still the same element.
      expect(host.contains(armButton as Node)).toBe(true);
      expect(armButton?.disabled).toBe(true);

      httpMock
        .expectOne((r) => r.url.includes('lineup/submit'))
        .flush({
          ...DRY,
          outcome: 'submitted',
          reason: '',
          submitted: true,
          saved_starters: 11,
        });
      fixture.detectChanges();
      await fixture.whenStable();

      const focused = document.activeElement as HTMLElement;
      expect(focused).not.toBe(document.body);
      expect(focused.classList.contains('feedback-block')).toBe(true);
      expect(focused.textContent).toContain('Submitted 4-3-3');

      // The quiet plan refresh `arm()` always fires.
      httpMock.expectOne((r) => r.url.includes('lineup/plan')).flush(NO_PLAN);
    });

    it('announces the dry run, and draws the arm control inside the same region', async () => {
      // The outcome lands in a live region that is already in the DOM, so it is announced
      // rather than merely rendered; the second act sits below the run it arms, so the run
      // on screen is always the run being armed.
      const fixture = await withPlan();
      fixture.componentInstance.runDry();
      httpMock.expectOne((r) => r.url.includes('lineup/submit')).flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();

      const live: HTMLElement | null = fixture.nativeElement.querySelector('[aria-live="polite"]');
      expect(live?.getAttribute('role')).toBe('status');
      expect(live?.textContent).toContain('Dry run — nothing was sent');
      expect(live?.textContent).toContain('Submit this lineup for real');
    });

    it('says a submit is unconfirmed when the read-back failed', async () => {
      // The POST returned 200 and the confirming GET did not. `submitted` is true — the
      // lineup is on the platform — so the page must not say "Not submitted", and must not
      // present `saved_starters` as evidence either: that number came from what we sent.
      const fixture = await withPlan();

      fixture.componentInstance.runDry();
      httpMock.expectOne((r) => r.url.includes('lineup/submit')).flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();

      fixture.componentInstance.arm();
      httpMock
        .expectOne((r) => r.url.includes('lineup/submit'))
        .flush({
          ...DRY,
          outcome: 'submitted',
          reason: '',
          submitted: true,
          saved_starters: 0,
          unconfirmed: 'apileague did not answer within 10s.',
        });
      fixture.detectChanges();
      await fixture.whenStable();

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('not confirmed');
      expect(text).toContain('apileague did not answer within 10s.');
      expect(text).not.toContain('Not submitted');
      expect(text).not.toContain('saved 0 starters');

      // The quiet refresh `arm()` always fires. Left unflushed it trips `httpMock.verify()`
      // in afterEach, which vitest then reports against whatever ran next.
      httpMock
        .expectOne((r) => r.url.includes('lineup/plan'))
        .flush({
          found: false,
          outcome: 'no_lineup',
          reason: 'x',
          module: '',
          matchday: null,
          starters: [],
          bench: [],
          bench_order: [],
          modules: [],
        });
    });

    it('drops a dry run when another lega is selected', async () => {
      // Carrying it across would let an operator arm a lineup they never saw.
      const fixture = await withPlan();
      fixture.componentInstance.runDry();
      httpMock.expectOne((r) => r.url.includes('lineup/submit')).flush(DRY);
      fixture.detectChanges();
      await fixture.whenStable();
      expect(fixture.componentInstance.canArm()).toBe(true);

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();

      expect(fixture.componentInstance.canArm()).toBe(false);
      httpMock
        .expectOne((r) => r.url.includes('lineup/plan'))
        .flush({
          found: false,
          outcome: 'no_lineup',
          reason: 'x',
          module: '',
          matchday: null,
          starters: [],
          bench: [],
        });
    });

    it('does not offer to arm a dry run that refused', async () => {
      const fixture = await withPlan();
      fixture.componentInstance.runDry();
      httpMock
        .expectOne((r) => r.url.includes('lineup/submit'))
        .flush({ ...DRY, outcome: 'no_matchday', reason: 'no coordinates yet' });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.canArm()).toBe(false);
      expect(fixture.nativeElement.textContent).toContain('No matchday context yet');
    });
  });
  describe('the scheduled history', () => {
    // What the launchd job did, read back. The operator looks here instead of at notifications,
    // so a failure has to stand out and a job that stopped running has to say so.
    function run(over: Partial<LineupRun> = {}): LineupRun {
      return {
        at: '2026-09-12T18:05:00+02:00',
        league: 4103937,
        scheduled: true,
        status: 'submitted',
        code: '',
        detail: '',
        module: '3421',
        matchday: 3,
        serie_a_matchday: 5,
        starters: ['Mandas'],
        bench: [],
        rejected: [],
        ...over,
      };
    }

    async function withRuns(body: LineupRuns) {
      const fixture = TestBed.createComponent(LineupComponent);
      fixture.detectChanges();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      httpMock.expectOne((r) => r.url.includes('lineup/runs')).flush(body);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('lists each run newest first, and a failure says why', async () => {
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 2,
        runs: [
          run({
            status: 'failed',
            code: 'TokenMissing',
            detail: 'no stored token for lega 4103937',
            module: '',
          }),
          run(),
        ],
        last_at: '2026-09-12T19:05:00+02:00',
        last_age_hours: 0.9,
      });

      const el: HTMLElement = fixture.nativeElement;
      const statuses = [...el.querySelectorAll('[data-run-status]')].map((n) =>
        n.getAttribute('data-run-status'),
      );
      expect(el.textContent).toContain('Automatic submits');
      expect(statuses).toEqual(['failed', 'submitted']);
      expect(el.textContent).toContain('no stored token for lega 4103937');
    });

    it('paints a failed run as danger, and never by colour alone', async () => {
      // The class carries the `error-container` / `on-error-container` pairing (it was
      // Tailwind's `text-danger` before the Material 3 conversion). The word and the icon
      // beside it are what a colour-blind reader goes by, so both are asserted too.
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [
          run({
            status: 'failed',
            code: 'not-armed',
            detail: 'not armed: FANTABOT_AUTO_ACT is false',
          }),
        ],
        last_age_hours: 0.5,
      });

      const label: HTMLElement | null = fixture.nativeElement.querySelector(
        '[data-run-status="failed"]',
      );
      expect(label?.classList).toContain('status-danger');
      expect(label?.textContent).toContain('Failed');
      expect(label?.querySelector('lucide-icon')).not.toBeNull();
    });

    it('warns when no scheduled run has happened for too long', async () => {
      // A job that stopped running writes nothing, so old green rows would read as fine.
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [run()],
        last_age_hours: 30,
        stale: true,
      });

      expect(fixture.nativeElement.textContent).toContain('may not be running');
    });

    it('says when nothing is recorded yet', async () => {
      const fixture = await withRuns(NO_RUNS);

      expect(fixture.nativeElement.textContent).toContain('No scheduled run recorded yet');
    });

    it('says when the record cannot be read, and where', async () => {
      const fixture = await withRuns({
        ...NO_RUNS,
        ok: false,
        exists: true,
        error: 'IsADirectoryError',
      });

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('IsADirectoryError');
      expect(text).toContain('/Users/x/.fantabot/lineup_runs.jsonl');
    });
  });
});
