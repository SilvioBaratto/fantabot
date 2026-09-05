import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { Corpus, SeedPanel } from '../../core/models/corpus';
import { JobList } from '../../core/models/job';
import { ICON_PROVIDER } from '../../icons';
import { HarvestComponent } from './harvest';

/**
 * The corpus panel is the instrument every later collection increment is graded on
 * (`todo/TODO.md` §2). What these tests hold it to is not that it renders numbers —
 * it is that the three things §1.1 cost a week are all readable off it: a format that
 * collected nothing, the gap between registered and followed, and the filter the
 * headline number survived.
 */
describe('HarvestComponent', () => {
  let httpMock: HttpTestingController;

  const CORPUS: Corpus = {
    ok: true,
    num_credits: 500,
    num_teams: 8,
    error: null,
    formats: [
      {
        asta_type: 'classic',
        rooms: 4386,
        rooms_with_events: 1186,
        events: 2145179,
        assignments: 148720,
        assignments_with_buyer: 131870,
        assignments_with_player: 144815,
        planner_sales: 32100,
      },
      {
        asta_type: 'mantra',
        rooms: 0,
        rooms_with_events: 0,
        events: 0,
        assignments: 0,
        assignments_with_buyer: 0,
        assignments_with_player: 0,
        planner_sales: 0,
      },
    ],
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HarvestComponent],
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

  const SEED: SeedPanel = {
    ok: true,
    path: '/Users/me/.fantabot/aste_live/seed.json',
    exists: true,
    mtime: '2026-09-05T18:04:00+00:00',
    rows: 1705,
    formats: { classic: 1226, mantra: 479 },
    error: null,
  };

  const NO_JOBS: JobList = { jobs: [] };

  async function render(body: Corpus = CORPUS, seed: SeedPanel = SEED, jobs: JobList = NO_JOBS) {
    const fixture = TestBed.createComponent(HarvestComponent);
    fixture.detectChanges(); // ngOnInit fires the reads
    httpMock.expectOne(`${environment.apiUrl}harvest/corpus`).flush(body);
    httpMock.expectOne(`${environment.apiUrl}harvest/seed`).flush(seed);
    httpMock.expectOne(`${environment.apiUrl}jobs`).flush(jobs);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  it('renders every count for a format that has one', async () => {
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('classic');
    expect(text).toContain('2,145,179'); // events
    expect(text).toContain('1,186'); // rooms with events
    expect(text).toContain('32,100'); // through the planner's filter
  });

  it('renders a format that has collected nothing as zeroes rather than dropping it', async () => {
    // The case the panel exists for. A missing row reads as "no data yet"; a row of
    // zeroes reads as "this format is empty", which is the finding.
    const fixture = await render();
    const text = fixture.nativeElement.textContent as string;

    expect(text).toContain('mantra');
    expect(fixture.nativeElement.querySelectorAll('[data-format]').length).toBe(2);
  });

  it('states the filter the headline number survived, on screen', async () => {
    // 32,100 is meaningless without `8 x 500, buyer and player link present`, and a
    // tooltip is not the contract — `read_plan_inputs` records why the shape is not a law.
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('500');
    expect(text).toContain('8');
    expect(text.toLowerCase()).toContain('buyer');
  });

  it('shows a reason, not an empty table, when the read fails', async () => {
    const fixture = TestBed.createComponent(HarvestComponent);
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}harvest/corpus`).error(new ProgressEvent('error'));
    httpMock.expectOne(`${environment.apiUrl}harvest/seed`).flush(SEED);
    httpMock.expectOne(`${environment.apiUrl}jobs`).flush(NO_JOBS);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('unreachable');
  });

  it('shows the database error the endpoint degraded open with', async () => {
    // `ok: false` is not the same as an unreachable API: the endpoint answered, and what
    // it answered is that it could not read. Rendering that as an empty table would hide
    // the one line that says why.
    const fixture = await render({ ...CORPUS, ok: false, formats: [], error: 'OperationalError' });

    expect(fixture.nativeElement.textContent).toContain('OperationalError');
  });

  // --- the seed panel, and the scan that fills it -------------------------------------

  it('renders the seed row count and its per-format split', async () => {
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('1,705');
    expect(text).toContain('1,226'); // classic
    expect(text).toContain('479'); // mantra
  });

  it('names the seed file and when it was last written', async () => {
    // The path is the one thing that says which landing zone this app is looking at,
    // and a scan run from a different working directory would write a different one.
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('/Users/me/.fantabot/aste_live/seed.json');
  });

  it('says the seed only ever grows, so a rising count is not evidence of collection', async () => {
    const text = ((await render()).nativeElement.textContent as string).toLowerCase();

    expect(text).toContain('never removes');
  });

  it('reports a seed that has never been written rather than zero rows', async () => {
    const fixture = await render(CORPUS, { ...SEED, exists: false, rows: 0, mtime: null, formats: {} });

    expect(fixture.nativeElement.textContent).toContain('No seed yet');
  });

  it('starts a scan and streams its lines', async () => {
    const fixture = await render();

    fixture.componentInstance.runScan();
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}actions/harvest-scan`)
      .flush({ job_id: 'j1' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.componentInstance.scanning()).toBe(true);
  });

  it('offers no format filter on the scan', async () => {
    // `--only` is a collection-time decision and this app must not be able to take it.
    // Scoped to the seed panel and checked as controls: the *load* panel legitimately
    // picks a format — a seed holds both and a load carries one — and the seed panel
    // legitimately prints "classic" and "mantra" while counting them.
    const seed = (await render()).nativeElement.querySelector('[data-panel="seed"]') as HTMLElement;

    expect(seed.querySelector('select')).toBeNull();
    expect(seed.querySelector('input')).toBeNull();
    expect(
      [...seed.querySelectorAll('button')].map((b) => (b.textContent ?? '').trim().toLowerCase()),
    ).toEqual(['scan live auctions']);
  });

  // --- the supervised loader ----------------------------------------------------------

  it('starts a supervised load and offers a stop while it runs', async () => {
    const fixture = await render();

    fixture.componentInstance.runLoad();
    fixture.detectChanges();
    const request = httpMock.expectOne(
      `${environment.apiUrl}harvest/load?asta_type=mantra&follow=true`,
    );
    request.flush({ job_id: 'L1' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(request.request.method).toBe('POST');
    expect(fixture.componentInstance.loaderRunning()).toBe(true);
  });

  it('asks the server to stop the load rather than forgetting it locally', async () => {
    // The child outlives the page: forgetting the id here would leave it running with
    // nothing left that knows how to stop it but the role lock.
    const fixture = await render();
    fixture.componentInstance.runLoad();
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}harvest/load?asta_type=mantra&follow=true`)
      .flush({ job_id: 'L1' });
    fixture.detectChanges();

    fixture.componentInstance.stopLoad();
    fixture.detectChanges();
    const stop = httpMock.expectOne(`${environment.apiUrl}jobs/L1/stop`);
    stop.flush({ ok: true });

    expect(stop.request.method).toBe('POST');
  });

  it('reattaches to a load that was already running', async () => {
    const fixture = await render(CORPUS, SEED, {
      jobs: [
        {
          id: 'L9',
          kind: 'harvest-load',
          status: 'running',
          started_at: '2026-09-05T18:00:00+00:00',
          line_count: 0,
          ok: null,
          stoppable: true,
        },
      ],
    });

    expect(fixture.componentInstance.loaderRunning()).toBe(true);
  });

  it('reattaches to a scan that was already running', async () => {
    const fixture = await render(CORPUS, SEED, {
      jobs: [
        {
          id: 'j9',
          kind: 'harvest-scan',
          status: 'running',
          started_at: '2026-09-05T18:00:00+00:00',
          line_count: 0,
          ok: null,
          stoppable: false,
        },
      ],
    });

    expect(fixture.componentInstance.scanning()).toBe(true);
  });
});