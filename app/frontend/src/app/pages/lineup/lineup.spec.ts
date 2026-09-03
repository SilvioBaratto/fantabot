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

    httpMock.expectOne((r) => r.url.includes('lineup/plan')).flush({
      found: true,
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

    httpMock.expectOne((r) => r.url.includes('lineup/plan')).flush({
      found: false,
      reason: 'Not connected, or no lineup available yet.',
      module: '',
      matchday: null,
      starters: [],
      bench: [],
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('Not connected');
  });
});
