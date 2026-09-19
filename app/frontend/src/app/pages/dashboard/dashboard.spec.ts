import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { DashboardComponent } from './dashboard';

describe('DashboardComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DashboardComponent],
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

  function overview(id: number) {
    return {
      league_id: id,
      league_name: null,
      captured_at: '2026-09-02T00:00:00Z',
      matchday: 3,
      budget: 500,
      roster_size: 25,
      min_roles: [2, 23],
      max_roles: [4, 28],
      modules: ['343'],
      bench_size: 12,
      team_count: 8,
    };
  }

  it('renders league overview cards', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('4103937');
    expect(text).toContain('8 teams');
    // Figure labels are sentence case now that they come from M3 content rules.
    expect(text).toContain('Roster size');
  });

  it('shows a progress bar only while a load is in flight', () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeTruthy();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeNull();
  });

  it('reports an unreachable API as an alert', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}lega`)
      .error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('API unreachable');
  });

  it('flips aria-expanded on the teams disclosure button', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    const toggle = fixture.nativeElement.querySelector(
      'button[aria-controls="teams-4103937"]',
    ) as HTMLButtonElement;
    expect(toggle).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    toggle.click();
    httpMock.expectOne(`${environment.apiUrl}lega/4103937/rosters`).flush([]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(fixture.nativeElement.querySelector('#teams-4103937')).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('No roster data');
  });

  it('loads rosters when teams are expanded', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.toggleTeams(4103937);
    httpMock.expectOne(`${environment.apiUrl}lega/4103937/rosters`).flush([
      {
        team_id: 1,
        nome: 'Squadra A',
        owner: 'Owner A',
        credits_initial: 500,
        credits_spent: 480,
        credits_remaining: 20,
        roster: [{ player_id: 1, cost: 50 }],
      },
    ]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('Squadra A');

    // The roster is a mat-table; its numbers still come from the same fields.
    const table = fixture.nativeElement.querySelector('table[mat-table]') as HTMLTableElement;
    expect(table).toBeTruthy();
    const cells = Array.from(table.querySelectorAll('tbody td')).map((c) =>
      (c.textContent ?? '').trim(),
    );
    expect(cells).toContain('480');
    expect(cells).toContain('20');
  });

  it('shows an empty state when there are no leagues', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No leagues yet');
  });
});
