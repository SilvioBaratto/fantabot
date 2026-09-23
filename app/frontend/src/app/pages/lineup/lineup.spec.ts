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
    // `null`, so a refusal fires no second read. The saved lineup belongs to a competition
    // and a refused plan has not resolved one.
    competition: null,
    starters: [],
    bench: [],
  };

  function overview(id: number) {
    return {
      league_id: id,
      league_name: 'Legamiallerotaie2',
      captured_at: null,
      matchday: null,
      competition: null,
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
        competition: null,
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
        competition: null,
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
        competition: null,
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
          competition: null,
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
      competition: null,
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
          competition: null,
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
          competition: null,
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
          competition: null,
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

    it('names the model that fell back, and the reason', async () => {
      // A row that says `submitted` and nothing else cannot be told apart from one where
      // the projection was asked for, failed, and the matcher's lineup went instead.
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [
          run({
            status: 'submitted',
            model: 'indexcompare',
            fallback: 'OpponentUnavailable',
          }),
        ],
      });

      const line: HTMLElement | null =
        fixture.nativeElement.querySelector('[data-run-fallback]');
      expect(line?.textContent).toContain('fell back to indexcompare');
      expect(line?.textContent).toContain('OpponentUnavailable');
    });

    it('lists every warning the run carried', async () => {
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [
          run({
            status: 'submitted',
            warnings: ['stale: the voti end at g3', 'sub mode assumed basic'],
          }),
        ],
      });

      const items = [
        ...fixture.nativeElement.querySelectorAll('[data-run-warnings] li'),
      ].map((n: Element) => n.textContent?.trim());
      expect(items).toEqual(['stale: the voti end at g3', 'sub mode assumed basic']);
    });

    it('summarises the shadow plan in one line', async () => {
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [
          run({
            status: 'submitted',
            model: 'indexcompare',
            shadow: {
              model: 'projection',
              module: '352',
              starter_ids: [1, 2, 3],
              bench_ids: [4],
              starters: ['A', 'B', 'C'],
              bench: ['D'],
              e_pts: 1.128,
              p_wdl: [0.288, 0.264, 0.448],
              e_fp: 70.64,
              sd: 5.85,
              cuts: [],
            },
          }),
        ],
      });

      const line: HTMLElement | null = fixture.nativeElement.querySelector('[data-run-shadow]');
      const text = line?.textContent?.replace(/\s+/g, ' ') ?? '';
      expect(text).toContain('projection');
      expect(text).toContain('352');
      expect(text).toContain('E[pts] 1.128');
      expect(text).toContain('P(W/D/L) 0.29/0.26/0.45');
      expect(text).toContain('E[fp] 70.6');
    });

    it('shows why a shadow was ranked on fantapunti rather than a bare zero', async () => {
      // `e_pts` is a float the row type pins, so a plan with no opponent writes 0.0 — and a
      // bare 0.000 reads as a certain loss. The cut is what stops it reading that way.
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [
          run({
            status: 'submitted',
            shadow: {
              model: 'projection',
              module: '343',
              starter_ids: [1],
              bench_ids: [],
              starters: ['A'],
              bench: [],
              e_pts: 0,
              p_wdl: [0, 0, 0],
              e_fp: 66.2,
              sd: 5,
              cuts: ['ranked on E[fp]: no opponent (none (only 3 calculated round(s)))'],
            },
          }),
        ],
      });

      const line: HTMLElement | null = fixture.nativeElement.querySelector('[data-run-shadow]');
      expect(line?.textContent).toContain('ranked on E[fp]: no opponent');
    });

    it('draws no fallback, warning or shadow line when there is none', async () => {
      // The control: these three are the whole of T38, and a template that always drew them
      // would make every row look like a fallback.
      const fixture = await withRuns({
        ...NO_RUNS,
        exists: true,
        total: 1,
        runs: [run()],
      });

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-run-fallback]')).toBeNull();
      expect(el.querySelector('[data-run-warnings]')).toBeNull();
      expect(el.querySelector('[data-run-shadow]')).toBeNull();
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

  // -- Switching lega while answers are in flight -----------------------------------------
  describe('a late answer for the lega just left', () => {
    /**
     * Two leghe on screen, A's plan already drawn and its saved read already answered.
     *
     * `A` is 4103937 (Mantra, the one that is played) and `B` is 3584692. Both `lineup/plan`
     * requests are matched on `league_id` rather than by order, because the point of these
     * tests is that the answers arrive out of order.
     */
    function planFor(nome: string, competition: number | null = null) {
      return {
        found: true,
        outcome: 'planned',
        reason: null,
        module: '343',
        matchday: 3,
        competition,
        starters: [{ player_id: 1, nome }],
        bench: [],
      };
    }

    const DRY_A = {
      outcome: 'not_armed',
      reason: 'the request did not ask to arm',
      module: '4-3-3',
      matchday: 3,
      competition: null,
      starters: [{ player_id: 1, nome: 'Svilar' }],
      bench: [],
      submitted: false,
      saved_starters: null,
      saved_at: null,
      rejected: [],
      past_deadline: null,
      unconfirmed: '',
    };

    async function twoLeghe() {
      const fixture = TestBed.createComponent(LineupComponent);
      fixture.detectChanges();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937), overview(3584692)]);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    function planReq(leagueId: number) {
      return httpMock.expectOne(
        (r) => r.url.includes('lineup/plan') && r.urlWithParams.includes(`league_id=${leagueId}`),
      );
    }

    it("does not draw the old lega's XI under the new one", async () => {
      const fixture = await twoLeghe();
      const slowA = planReq(4103937);

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi'));
      fixture.detectChanges();
      await fixture.whenStable();

      slowA.flush(planFor('Mandas'));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.plan()?.starters[0].nome).toBe('Carnesecchi');
    });

    it('chains no saved read for a lega nobody is looking at', async () => {
      // The second hop is the one the outer guard cannot see: it starts from inside the
      // plan's own `next`, so it was legitimate when it left. An outstanding request also
      // fails `httpMock.verify()`, which is the recorded cascade hazard.
      const fixture = await twoLeghe();
      const slowA = planReq(4103937);

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi', null));
      fixture.detectChanges();
      await fixture.whenStable();

      slowA.flush(planFor('Mandas', 12));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(httpMock.match((r) => r.url.includes('lineup/current')).length).toBe(0);
    });

    it("does not blank the new lega's skeleton when the old one fails", async () => {
      const fixture = await twoLeghe();
      const slowA = planReq(4103937);

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();

      slowA.error(new ProgressEvent('net'));
      fixture.detectChanges();
      await fixture.whenStable();

      // Read *before* asserting, and flush B before the expectation runs. An assertion that
      // precedes its flush leaves an outstanding request when it fails, `verify()` fails in
      // `afterEach`, and the TestBed stays instantiated — the recorded cascade that takes
      // down every spec after it. A RED test must fail alone.
      const blankedWhileBWaited = fixture.componentInstance.planLoading();
      planReq(3584692).flush(planFor('Carnesecchi'));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(blankedWhileBWaited).toBe(true);
    });

    it("does not wear the new lega's names on the old one's saved XI", async () => {
      // The second hop needs its own guard and this is the case that proves it: A's plan
      // lands while A is still selected, so the outer guard passes and `fetchCurrent(A)`
      // leaves legitimately. The switch happens *after* that. `currentNamed` joins these
      // ids against whatever plan is on screen, so A's saved XI comes back wearing B's
      // names.
      const fixture = await twoLeghe();
      planReq(4103937).flush(planFor('Mandas', 12));
      fixture.detectChanges();
      await fixture.whenStable();
      const slowCurrentA = httpMock.expectOne((r) => r.url.includes('lineup/current'));

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi', null));
      fixture.detectChanges();
      await fixture.whenStable();

      slowCurrentA.flush({
        outcome: 'read',
        reason: null,
        starters: [{ player_id: 1 }],
        saved_at: null,
      });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.current()).toBeNull();
    });

    it("does not report the old lega's failed saved read under the new one", async () => {
      const fixture = await twoLeghe();
      planReq(4103937).flush(planFor('Mandas', 12));
      fixture.detectChanges();
      await fixture.whenStable();
      const slowCurrentA = httpMock.expectOne((r) => r.url.includes('lineup/current'));

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi', null));
      fixture.detectChanges();
      await fixture.whenStable();

      slowCurrentA.error(new ProgressEvent('net'));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.currentError()).toBeNull();
    });

    it('a dry run for the lega just left cannot arm the new one', async () => {
      // The only one of these that reaches an irreversible action. `select` nulls `dryRun`
      // (`lineup.ts`) precisely so an operator cannot arm a lineup they never saw — but a
      // dry run still in flight lands *after* that null and puts it back, now under the new
      // lega's heading. `arm()` then reads `selectedId()`, which is the new one.
      const fixture = await twoLeghe();
      planReq(4103937).flush(planFor('Mandas'));
      fixture.detectChanges();
      await fixture.whenStable();

      fixture.componentInstance.runDry();
      const dryA = httpMock.expectOne((r) => r.url.includes('lineup/submit'));
      expect(dryA.request.body).toEqual({ league_id: 4103937, arm: false });

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi'));
      fixture.detectChanges();
      await fixture.whenStable();

      dryA.flush(DRY_A);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.dryRun()).toBeNull();
      expect(fixture.componentInstance.canArm()).toBe(false);
    });

    it('a late dry run still frees the buttons', async () => {
      // `submitting` is page-wide, not per-lega. Guarding before clearing it would leave
      // both controls disabled for ever — the page wedged rather than merely wrong.
      const fixture = await twoLeghe();
      planReq(4103937).flush(planFor('Mandas'));
      fixture.detectChanges();
      await fixture.whenStable();

      fixture.componentInstance.runDry();
      const dryA = httpMock.expectOne((r) => r.url.includes('lineup/submit'));

      fixture.componentInstance.select(3584692);
      fixture.detectChanges();
      planReq(3584692).flush(planFor('Carnesecchi'));
      fixture.detectChanges();
      await fixture.whenStable();

      dryA.flush(DRY_A);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.submitting()).toBe(false);
    });
  });

  // -- `fantabot lineup show`, on a screen ------------------------------------------------
  describe('saved on the platform', () => {
    function planned(competition: number | null) {
      return {
        found: true,
        outcome: 'planned',
        reason: null,
        module: '343',
        matchday: 3,
        competition,
        starters: [{ player_id: 1, nome: 'Mandas' }],
        bench: [{ player_id: 9, nome: 'Rovella' }],
      };
    }

    async function ready(competition: number | null = 12) {
      const fixture = TestBed.createComponent(LineupComponent);
      fixture.detectChanges();
      httpMock.expectOne((r) => r.url.includes('lineup/runs')).flush({ runs: [] });
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock.expectOne((r) => r.url.includes('lineup/plan')).flush(planned(competition));
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('has the saved status region in the DOM, empty, when there is nothing to say', async () => {
      // Same rule as `news.spec.ts`: the refusal region was created with its own text, so
      // "nothing saved yet" announced unreliably. It is the ordinary state before a
      // matchday's first submit, which is why it stays `status` and does not become `alert`.
      const fixture = await ready(12);
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush({ outcome: 'read', reason: '', module: '343', starters: [], bench: [] });
      fixture.detectChanges();
      await fixture.whenStable();

      const region = fixture.nativeElement.querySelector(
        '[data-testid="saved-lineup"] [role="status"]',
      );
      expect(region).not.toBeNull();
      expect(region?.matches(':empty')).toBe(true);
    });

    it('reads the competition the plan resolved, never one of its own', async () => {
      // The two screens are only comparable when both name the same competition — and a
      // page that picked one would be answering a question nobody asked.
      const fixture = await ready(12);

      const asked = httpMock.expectOne((r) => r.url.includes('lineup/current'));
      expect(asked.request.urlWithParams).toContain('competition=12');
      expect(asked.request.urlWithParams).toContain('league_id=4103937');
      asked.flush({ outcome: 'read', reason: '', module: '343', starters: [1], bench: [9] });
      fixture.detectChanges();
      await fixture.whenStable();
    });

    it('says why it asked for nothing, rather than claiming to be reading', async () => {
      // `fetchCurrent` only fires once the plan names a competition, so on a refused plan
      // nothing is ever asked — and "Reading…" is a sentence about a request that does not
      // exist. A panel that cannot read and a competition with nothing saved look identical
      // otherwise, which is the one thing this card is for.
      const fixture = await ready(null);

      const note = fixture.nativeElement.querySelector('[data-testid="saved-unasked"]');
      expect(note).not.toBeNull();
      expect(note.textContent).toContain('resolved no competition');
      expect(fixture.nativeElement.textContent).not.toContain('Reading what the platform');
    });

    it('asks for nothing when the plan resolved no competition', async () => {
      // A refused plan has not resolved one, and `undefined !== null` — which is how an
      // outstanding request for `competition=undefined` takes down three unrelated specs.
      await ready(null);

      httpMock.expectNone((r) => r.url.includes('lineup/current'));
    });

    it('names the saved ids from the plan it already holds', async () => {
      // The server returns ids: naming them there would mean running the whole plan to
      // annotate a read that has already answered. The join here is free.
      const fixture = await ready();
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush({ outcome: 'read', reason: '', module: '343', starters: [1], bench: [9] });
      fixture.detectChanges();
      await fixture.whenStable();

      const starters = fixture.nativeElement.querySelector('[data-testid="saved-starters"]');
      expect(starters.textContent).toContain('Mandas');
      expect(
        fixture.nativeElement.querySelector('[data-testid="saved-bench"]').textContent,
      ).toContain('Rovella');
    });

    it('falls back to the id for a saved player the plan never named', async () => {
      // Which is what a player outside the planned roster looks like, and is worth seeing
      // as such: the platform is holding somebody the plan would not field.
      const fixture = await ready();
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush({ outcome: 'read', reason: '', module: '343', starters: [4242], bench: [] });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(
        fixture.nativeElement.querySelector('[data-testid="saved-starters"]').textContent,
      ).toContain('4242');
    });

    it('draws no panel at all before a lega is selected', async () => {
      // A card that asks a question about nothing. It rendered a heading and a subtitle on
      // a fresh install, under "No leagues yet".
      const fixture = TestBed.createComponent(LineupComponent);
      fixture.detectChanges();
      httpMock.expectOne((r) => r.url.includes('lineup/runs')).flush({ runs: [] });
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.querySelector('[data-testid="saved-lineup"]')).toBeNull();
    });

    it('announces "nothing saved yet" as status, never as an alert', async () => {
      // It is the ordinary state before a matchday's first submit. `role="alert"` would
      // interrupt a screen-reader user to report that nothing has happened.
      const fixture = await ready();
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush({
          outcome: 'no_lineup',
          reason: 'no lineup has been saved for this competition yet.',
          module: '',
          starters: [],
          bench: [],
        });
      fixture.detectChanges();
      await fixture.whenStable();

      const note = fixture.nativeElement.querySelector('[data-testid="saved-lineup"] [role]');
      expect(note?.getAttribute('role')).toBe('status');
      expect(
        fixture.nativeElement.querySelector('[data-testid="saved-lineup"] [role="alert"]'),
      ).toBeNull();
    });

    it('shows the server’s reason and no lineup when nothing is saved', async () => {
      // Not an error and not an empty XI: a competition whose lineup has never been set is
      // the ordinary state before a matchday's first submit.
      const fixture = await ready();
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush({
          outcome: 'no_lineup',
          reason: 'no lineup has been saved for this competition yet.',
          module: '',
          starters: [],
          bench: [],
        });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('has been saved');
      expect(fixture.nativeElement.querySelector('[data-testid="saved-starters"]')).toBeNull();
    });

    it('keeps the plan when the saved read fails', async () => {
      // The plan beside it is still the answer to its own question; blanking the page over
      // a secondary read would lose it.
      const fixture = await ready();
      httpMock
        .expectOne((r) => r.url.includes('lineup/current'))
        .flush('nope', { status: 500, statusText: 'Server Error' });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.plan()?.module).toBe('343');
      expect(fixture.componentInstance.currentError()).toContain('Could not reach the API');
    });
  });
});
