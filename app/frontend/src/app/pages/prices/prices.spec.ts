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

  /** A report with both lists and a flag count, so every section of the page renders. */
  function priced(system: string, stored = 0) {
    return {
      found: true,
      outcome: 'priced',
      reason: null,
      system,
      stored,
      fades: [],
      biggest_bumps: [tp('Dybala', 20, 28)],
      biggest_cuts: [tp('Someone', 15, 8)],
      flag_counts: { floor_qi: 3 },
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

    // Pressed, not called: the button is a Material filled button now, and the test that
    // says "presses the button" should go through the element the operator presses.
    const refit = fixture.nativeElement.querySelector(
      '[data-refit-store]',
    ) as HTMLButtonElement | null;
    expect(refit).toBeTruthy();
    expect(refit!.disabled).toBe(false);
    refit!.click();

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

  it('tells a misspelt listone from a missing one', async () => {
    // `system` reaches a `WHERE listone = :system`, so a typo used to select no rows and
    // read as "no data" — sending the operator to scrape a season when the fix is a
    // spelling. Rendered since 1.7 and untested until 1.18.
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();

    httpMock
      .expectOne((r) => r.url.includes('target-prices'))
      .flush({
        found: false,
        outcome: 'unknown_system',
        reason: "unknown system 'mantr'; expected one of classic, mantra",
        system: 'mantr',
        stored: 0,
        fades: [],
        biggest_bumps: [],
        biggest_cuts: [],
        flag_counts: {},
      });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('That is not a listone');
    expect(text).toContain('classic, mantra');
  });

  it('re-reads the report when the listone changes', async () => {
    // The system picker was two colour-only buttons; it is a single-selection
    // mat-button-toggle-group, so the current listone is carried by aria-checked and a
    // check mark rather than by a background colour.
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.includes('target-prices')).flush(priced('classic'));
    fixture.detectChanges();
    await fixture.whenStable();

    const mantra = fixture.nativeElement.querySelector(
      'mat-button-toggle[data-system="mantra"] button',
    ) as HTMLButtonElement | null;
    expect(mantra).toBeTruthy();
    expect(mantra!.getAttribute('aria-checked')).toBe('false');

    mantra!.click();
    const second = httpMock.expectOne((r) => r.url.includes('target-prices'));
    expect(second.request.method).toBe('GET');
    expect(second.request.urlWithParams).toContain('system=mantra');
    second.flush(priced('mantra'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.componentInstance.system()).toBe('mantra');
    expect(mantra!.getAttribute('aria-checked')).toBe('true');
  });

  it('renders each mover list as a table with column and row headers', async () => {
    // The two lists were nested divs on a CSS grid, which reads as one run-on line to a
    // screen reader. They are real tables: the player is the row header and the two
    // numbers are their own cells, in the `col-num` column the stylesheet right-aligns
    // and sets in tabular figures.
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.includes('target-prices')).flush(priced('classic'));
    fixture.detectChanges();
    await fixture.whenStable();

    const bumps = fixture.nativeElement.querySelector(
      'table[aria-labelledby="bumps-title"]',
    ) as HTMLTableElement | null;
    expect(bumps).toBeTruthy();
    expect(
      Array.from(bumps!.querySelectorAll('thead th[scope="col"]')).map((h) =>
        (h.textContent ?? '').trim(),
      ),
    ).toEqual(['Player', 'QI', 'Target']);

    const row = bumps!.querySelector('tbody tr') as HTMLTableRowElement;
    expect((row.querySelector('th[scope="row"]')?.textContent ?? '').trim()).toContain('Dybala');
    expect(
      Array.from(row.querySelectorAll('td.col-num')).map((c) => (c.textContent ?? '').trim()),
    ).toEqual(['20', '28']);
  });

  it('gives the page one h1 and a heading for every section', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.includes('target-prices')).flush(priced('classic'));
    fixture.detectChanges();
    await fixture.whenStable();

    const h1s = Array.from(fixture.nativeElement.querySelectorAll('h1')) as HTMLElement[];
    expect(h1s.map((h) => (h.textContent ?? '').trim())).toEqual(['Target prices']);

    const h2s = Array.from(fixture.nativeElement.querySelectorAll('h2')) as HTMLElement[];
    expect(h2s.map((h) => (h.textContent ?? '').trim())).toEqual([
      'Biggest bumps',
      'Biggest cuts',
      'Flags',
    ]);
  });

  it('shows a progress bar and disables the button while a fit is in flight', async () => {
    const fixture = TestBed.createComponent(PricesComponent);
    fixture.detectChanges();
    httpMock.expectOne((r) => r.url.includes('target-prices')).flush(priced('classic'));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeNull();

    const refit = fixture.nativeElement.querySelector('[data-refit-store]') as HTMLButtonElement;
    refit.click();
    // No `whenStable` here: the POST is still open and HttpClient holds a pending task.
    fixture.detectChanges();

    const write = httpMock.expectOne((r) => r.url.includes('target-prices'));
    expect(write.request.method).toBe('POST');
    expect(refit.disabled).toBe(true);
    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeTruthy();
    // The wait is announced once, from the page's one live region.
    const status = fixture.nativeElement.querySelector('[role="status"]') as HTMLElement;
    expect((status.textContent ?? '').trim()).toBe('Storing target prices');

    write.flush(priced('classic', 1142));
    fixture.detectChanges();
    await fixture.whenStable();

    expect(refit.disabled).toBe(false);
    expect(fixture.nativeElement.querySelector('mat-progress-bar')).toBeNull();
  });
});
