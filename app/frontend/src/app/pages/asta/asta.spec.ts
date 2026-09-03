import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { AstaComponent } from './asta';

describe('AstaComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AstaComponent],
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

  it('auto-selects the first lega and renders its plan', async () => {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock.expectOne((r) => r.url.includes('asta/plan')).flush({
      found: true,
      listone: 'mantra',
      roster_size: 30,
      total_cost: 500,
      objective: 1897,
      budget: 500,
      players: [{ player_id: '1', nome: 'Svilar', price: 20 }],
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Svilar');
    expect(text).toContain('objective');
  });

  it('shows a no-plan state when the pool is empty', async () => {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock.expectOne((r) => r.url.includes('asta/plan')).flush({
      found: false,
      listone: '',
      roster_size: 0,
      total_cost: 0,
      objective: 0,
      budget: 0,
      players: [],
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No plan yet');
  });
});
