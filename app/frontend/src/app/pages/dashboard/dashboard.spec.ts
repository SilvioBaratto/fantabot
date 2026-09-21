import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { LegaOverview } from '../../core/models/lega';
import { DashboardComponent } from './dashboard';

describe('DashboardComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DashboardComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
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

  function overview(id: number, extra: Partial<LegaOverview> = {}): LegaOverview {
    return {
      league_id: id,
      league_name: null,
      captured_at: '2026-09-02T00:00:00Z',
      format: 'mantra',
      matchday: 3,
      budget: 500,
      roster_size: 25,
      min_roles: [2, 23],
      max_roles: [4, 28],
      modules: ['343'],
      bench_size: 12,
      team_count: 8,
      ...extra,
    };
  }

  function team(id: number, nome: string) {
    return {
      team_id: id,
      nome,
      owner: `Owner of ${nome}`,
      credits_initial: 500,
      credits_spent: 480,
      credits_remaining: 20,
      roster: [{ player_id: id, cost: 50 }],
    };
  }

  async function render(leagues: LegaOverview[]) {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush(leagues);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  function toggleFor(root: HTMLElement, leagueId: number): HTMLButtonElement {
    const card = root.querySelectorAll('mat-card.lega-card');
    const match = Array.from(card).find((c) => c.textContent?.includes(`#${leagueId}`));
    return match!.querySelector('mat-card-actions button') as HTMLButtonElement;
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
    httpMock.expectOne(`${environment.apiUrl}lega`).error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain("Couldn't load your leghe");
  });

  it('keeps the loaded cards when a refresh fails', async () => {
    const fixture = await render([overview(4103937)]);

    fixture.componentInstance.load();
    httpMock.expectOne(`${environment.apiUrl}lega`).error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(
      "Couldn't refresh your leghe",
    );
    expect(root.querySelectorAll('mat-card.lega-card').length).toBe(1);

    // The next good answer clears the error and leaves the cards.
    fixture.componentInstance.load();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    expect(root.querySelector('[role="alert"]')).toBeNull();
    expect(root.querySelectorAll('mat-card.lega-card').length).toBe(1);
  });

  it('supersedes an in-flight load when Refresh is pressed again', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    const first = httpMock.expectOne(`${environment.apiUrl}lega`);

    fixture.componentInstance.load();
    expect(first.cancelled).toBe(true);
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
  });

  it('flips aria-expanded on the teams disclosure button', async () => {
    const fixture = TestBed.createComponent(DashboardComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    const toggle = toggleFor(fixture.nativeElement, 4103937);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    // Collapsed, the region is not in the DOM, so the IDREF would dangle.
    expect(toggle.getAttribute('aria-controls')).toBeNull();

    toggle.click();
    httpMock.expectOne(`${environment.apiUrl}lega/4103937/rosters`).flush([]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-controls')).toBe('teams-4103937');
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

  it('shows an empty state that leads to Synchronize', async () => {
    const fixture = await render([]);

    expect(fixture.nativeElement.textContent).toContain('No leagues yet');
    const link = fixture.nativeElement.querySelector('a[href="/synchronize"]') as HTMLElement;
    expect(link?.textContent?.trim()).toBe('Go to Synchronize');
  });

  it("draws each lega's teams in its own region, whatever order the answers arrive in", async () => {
    const fixture = await render([overview(1), overview(2)]);
    const root = fixture.nativeElement as HTMLElement;

    toggleFor(root, 1).click();
    const first = httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`);
    toggleFor(root, 2).click();
    const second = httpMock.expectOne(`${environment.apiUrl}lega/2/rosters`);

    // Lega 1's answer lands last: it must not overwrite what lega 2's region shows.
    second.flush([team(20, 'Squadra Due')]);
    first.flush([team(10, 'Squadra Uno')]);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(root.querySelector('#teams-1')?.textContent).toContain('Squadra Uno');
    expect(root.querySelector('#teams-1')?.textContent).not.toContain('Squadra Due');
    expect(root.querySelector('#teams-2')?.textContent).toContain('Squadra Due');
    expect(root.querySelector('#teams-2')?.textContent).not.toContain('Squadra Uno');
  });

  it('cancels a roster read when its region is closed', async () => {
    const fixture = await render([overview(1)]);
    const toggle = toggleFor(fixture.nativeElement, 1);

    toggle.click();
    const pending = httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`);
    toggle.click();
    expect(pending.cancelled).toBe(true);
  });

  it('says a failed roster read failed, and hands focus to the toggle on retry', async () => {
    const fixture = await render([overview(1)]);
    const root = fixture.nativeElement as HTMLElement;
    const toggle = toggleFor(root, 1);

    toggle.click();
    httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`).error(new ProgressEvent('error'));
    fixture.detectChanges();
    await fixture.whenStable();

    const region = root.querySelector('#teams-1') as HTMLElement;
    expect(region.textContent).toContain("Couldn't load the teams");
    expect(region.textContent).not.toContain('No roster data');

    const retry = Array.from(region.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Try again'),
    ) as HTMLButtonElement;
    retry.focus();
    retry.click();
    fixture.detectChanges();

    expect(document.activeElement).toBe(toggle);
    httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`).flush([team(10, 'Squadra Uno')]);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(root.querySelector('#teams-1')?.textContent).toContain('Squadra Uno');
  });

  it('re-reads an open region on Refresh, and closes one whose lega is gone', async () => {
    const fixture = await render([overview(1), overview(2)]);
    const root = fixture.nativeElement as HTMLElement;
    toggleFor(root, 1).click();
    httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`).flush([]);
    toggleFor(root, 2).click();
    httpMock.expectOne(`${environment.apiUrl}lega/2/rosters`).flush([]);

    fixture.componentInstance.load();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(1)]);
    httpMock.expectOne(`${environment.apiUrl}lega/1/rosters`).flush([]);
    fixture.detectChanges();

    expect(fixture.componentInstance.isExpanded(1)).toBe(true);
    expect(fixture.componentInstance.isExpanded(2)).toBe(false);
  });

  it('labels a Classic role band with P/D/C/A and speaks the role names', async () => {
    const fixture = await render([
      overview(3584692, { format: 'classic', min_roles: [3, 8, 8, 6], max_roles: [3, 8, 8, 6] }),
    ]);
    const groups = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('.band-group'),
    );

    expect(groups.map((g) => g.querySelector('.band-label')?.textContent?.trim())).toEqual([
      'P',
      'D',
      'C',
      'A',
    ]);
    expect(groups.map((g) => g.querySelector('.sr-only')?.textContent?.trim())).toEqual([
      'Goalkeepers',
      'Defenders',
      'Midfielders',
      'Forwards',
    ]);
    // A band whose floor and ceiling agree is one number, not "3–3".
    expect(groups.map((g) => g.querySelector('.band-range')?.textContent?.trim())).toEqual([
      '3',
      '8',
      '8',
      '6',
    ]);
    expect(groups[0].querySelector('.band-label')?.getAttribute('aria-hidden')).toBe('true');
    // What a screen reader hears per group: the role name, then its count.
    expect(groups[0].querySelector('.sr-only')?.textContent).toBe('Goalkeepers ');
  });

  it('labels a Mantra role band by group, with en-dash ranges', async () => {
    const fixture = await render([overview(4103937)]);
    const texts = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('.band-group'),
    ).map((g) => g.textContent?.trim());

    expect(texts).toEqual(['Goalkeepers 2–4', 'Outfield 23–28']);
  });

  it('leaves a band unlabelled when the format is unknown', () => {
    const band = TestBed.createComponent(DashboardComponent).componentInstance.roleBand(
      overview(1, { format: null }),
    );
    expect(band).toEqual([
      { label: null, spoken: null, range: '2–4' },
      { label: null, spoken: null, range: '23–28' },
    ]);
  });

  it('never labels a band whose group count disagrees with its format', () => {
    const band = TestBed.createComponent(DashboardComponent).componentInstance.roleBand(
      overview(1, { format: 'classic', min_roles: [2, 23], max_roles: [4, 28] }),
    );
    expect(band?.every((g) => g.label === null)).toBe(true);
  });

  it('says when each lega was last synced', async () => {
    const fixture = await render([overview(1), overview(2, { captured_at: null })]);
    const root = fixture.nativeElement as HTMLElement;

    const time = root.querySelector('time') as HTMLTimeElement;
    expect(time.getAttribute('datetime')).toBe('2026-09-02T00:00:00Z');
    expect(time.textContent).toContain('Synced');
    expect(root.textContent).toContain('Never synced');
  });
});
