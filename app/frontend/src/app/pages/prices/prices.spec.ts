import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { PricesComponent } from './prices';

describe('PricesComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PricesComponent],
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

  function tp(nome: string, qi: number, target: number) {
    return {
      id: nome,
      nome,
      squadra: 'ROM',
      role: 'A',
      macro_role: 'A',
      qi,
      prior_media_fantavoto: 6.5,
      predicted_pct_delta: 0.1,
      team_factor: 1,
      target_price: target,
      flags: '',
    };
  }

  it('renders bumps and cuts for the selected system', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.includes('target-prices'))
      .flush({
        found: true,
        outcome: 'priced',
        reason: null,
        system: 'classic',
        stored: 42,
        fades: [],
        biggest_bumps: [tp('Dybala', 20, 28)],
        biggest_cuts: [tp('Someone', 15, 8)],
        flag_counts: { floor_qi: 3 },
      });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Dybala');
    expect(text).toContain('Biggest bumps');
    expect(text).toContain('42 prices stored');
  });

  it('shows an empty state when the model has no data', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.includes('target-prices'))
      .flush({
        found: false,
        outcome: 'no_data',
        reason: 'no classic training data — the fit needs `statistiche` for the training seasons.',
        system: 'classic',
        stored: 0,
        fades: [],
        biggest_bumps: [],
        biggest_cuts: [],
        flag_counts: {},
      });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('No target prices');
    // The endpoint's own reason. "No data" and "the database would not open" need
    // different remedies, and used to render identically.
    expect(text).toContain('statistiche');
  });

  it('tells no data from a database that would not open', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.includes('target-prices'))
      .flush({
        found: false,
        outcome: 'unreachable',
        reason: 'OperationalError: could not connect to server',
        system: 'classic',
        stored: 0,
        fades: [],
        biggest_bumps: [],
        biggest_cuts: [],
        flag_counts: {},
      });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('Could not reach the database');
  });

  it('does not store anything just by opening the page', async () => {
    // The GET behind this page called `pricing.run`, which upserts `target_price` — so
    // arriving here stored a fit nobody asked for. A GET that writes is not a slow GET.
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();

    const request = httpMock.expectOne((r) => r.url.includes('target-prices'));
    expect(request.request.method).toBe('GET');
    request.flush({
      found: true,
      outcome: 'priced',
      reason: null,
      system: 'classic',
      stored: 0,
      fades: [],
      biggest_bumps: [tp('Dybala', 20, 28)],
      biggest_cuts: [],
      flag_counts: {},
    });
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock.verify();
  });

  it('stores only when the operator presses the button', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();
    httpMock
      .expectOne((r) => r.url.includes('target-prices'))
      .flush({
        found: true,
        outcome: 'priced',
        reason: null,
        system: 'classic',
        stored: 0,
        fades: [],
        biggest_bumps: [tp('Dybala', 20, 28)],
        biggest_cuts: [],
        flag_counts: {},
      });
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.store();
    const write = httpMock.expectOne((r) => r.url.includes('target-prices'));
    expect(write.request.method).toBe('POST');
    write.flush({
      found: true,
      outcome: 'priced',
      reason: null,
      system: 'classic',
      stored: 1142,
      fades: [],
      biggest_bumps: [tp('Dybala', 20, 28)],
      biggest_cuts: [],
      flag_counts: {},
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('1142 prices stored');
  });
});
