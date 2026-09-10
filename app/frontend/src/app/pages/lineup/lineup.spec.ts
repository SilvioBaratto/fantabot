import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { environment } from '../../../environments/environment';
import { LineupComponent } from './lineup';

describe('LineupComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LineupComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

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
});
