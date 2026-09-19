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
 * (T15). What these tests hold it to is not that it renders numbers —
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
    default_pool: 1000,
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
    //
    // `mat-select` is checked alongside `select`: the Material 3 conversion replaced the
    // native element, so a tag-only assertion would have gone quietly vacuous.
    const seed = (await render()).nativeElement.querySelector('[data-panel="seed"]') as HTMLElement;

    expect(seed.querySelector('select')).toBeNull();
    expect(seed.querySelector('mat-select')).toBeNull();
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

  it('keeps the format a parameter of the load, and offers it nowhere else', async () => {
    // The positive half of the two "no format selector here" tests. Without it, replacing
    // the native `<select>` with a `mat-select` would have left three assertions that pass
    // because the element they name no longer exists anywhere.
    const root = (await render()).nativeElement as HTMLElement;
    const loader = root.querySelector('[data-panel="loader"]') as HTMLElement;

    expect(loader.querySelector('mat-select')).not.toBeNull();
    expect(root.querySelectorAll('mat-select').length).toBe(1);
    expect(root.querySelector('select')).toBeNull();
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

  // --- the collector ------------------------------------------------------------------

  it('pre-fills the pool above the population, never at the default below it', async () => {
    // 1,705 rows against a default of 1,000. A pool below the population is silent
    // starvation: a watcher on a live evening does not finish, so a queued auction never
    // gets a permit and never connects. That cost 145 of 395 auctions on 2026-08-27.
    const fixture = await render();

    expect(fixture.componentInstance.pool()).toBe(1705);
  });

  it('takes the default pool from the server rather than hardcoding it', async () => {
    const fixture = await render(CORPUS, { ...SEED, rows: 12, default_pool: 1000 });

    expect(fixture.componentInstance.pool()).toBe(1000);
  });

  it('starts a collect with the pool it shows', async () => {
    const fixture = await render();

    fixture.componentInstance.runCollect();
    fixture.detectChanges();
    const request = httpMock.expectOne(`${environment.apiUrl}harvest/collect?pool=1705`);
    request.flush({ job_id: 'C1' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.componentInstance.collecting()).toBe(true);
  });

  it("shows the server's refusal verbatim when the pool is below the population", async () => {
    // The refusal carries both numbers. Replacing it with a generic message here is how
    // the one fact the operator needs stops reaching them.
    const fixture = await render();

    fixture.componentInstance.runCollect();
    fixture.detectChanges();
    httpMock
      .expectOne(`${environment.apiUrl}harvest/collect?pool=1705`)
      .flush({ detail: 'pool is 1000 and the seed holds 1705 auction(s)' }, { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('1705');
    expect(fixture.componentInstance.collecting()).toBe(false);
  });

  it('says on screen that the CLI warns where the app refuses, and why', async () => {
    // 2.3(a). The divergence is deliberate, and a deliberate divergence that lives only in
    // a docstring is one the operator discovers as a bug. A warning at a terminal reaches
    // whoever typed the command; the same warning at 21:00 in a browser reaches nobody,
    // and the run it precedes is three hours long. 145 of 395 auctions, 2026-08-27.
    const text = (await render()).nativeElement.textContent as string;

    expect(text).toContain('warns');
    expect(text).toContain('145');
    expect(text).toContain('395');
  });

  it('offers no format selector on the collector', async () => {
    // `from_seed_row` reads each row's own format, so one seed carries both.
    const panel = (await render()).nativeElement.querySelector(
      '[data-panel="collector"]',
    ) as HTMLElement;

    expect(panel.querySelector('select')).toBeNull();
    expect(panel.querySelector('mat-select')).toBeNull();
  });

  it('shows progress and a stop that names what it stops while the collector runs', async () => {
    // Two buttons reading "Stop" on one page is the ambiguity the M3 content rules are
    // about; the progress bar is what replaced a spinner glued inside the start button.
    const fixture = await render();

    fixture.componentInstance.runCollect();
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}harvest/collect?pool=1705`).flush({ job_id: 'C1' });
    fixture.detectChanges();
    await fixture.whenStable();

    const panel = fixture.nativeElement.querySelector(
      '[data-panel="collector"]',
    ) as HTMLElement;

    expect(panel.querySelector('mat-progress-bar')).not.toBeNull();
    expect(
      [...panel.querySelectorAll('button')].map((b) => (b.textContent ?? '').trim()),
    ).toContain('Stop collector');
  });

  it('puts the streamed log in a named region the keyboard can reach', async () => {
    // A scroll region a wheel is the only way into is unreachable from the keyboard
    // (WCAG 2.1.1), and a collector log on a live evening is the longest one here.
    const fixture = await render();
    fixture.componentInstance.collectLines.set(['connected', 'lot 12 assigned']);
    fixture.detectChanges();

    const log = fixture.nativeElement.querySelector(
      '[data-panel="collector"] [role="region"]',
    ) as HTMLElement;

    expect(log).not.toBeNull();
    expect(log.getAttribute('aria-label')).toBe('Collector log');
    expect(log.getAttribute('tabindex')).toBe('0');
    expect(log.textContent).toContain('lot 12 assigned');
  });

  it('has exactly one h1 and skips no heading level', async () => {
    const root = (await render()).nativeElement as HTMLElement;
    const levels = [...root.querySelectorAll('h1, h2, h3, h4, h5, h6')].map((h) =>
      Number(h.tagName.slice(1)),
    );

    expect(levels.filter((level) => level === 1).length).toBe(1);
    expect(levels[0]).toBe(1);
    levels.slice(1).forEach((level, index) => {
      expect(level - levels[index]).toBeLessThanOrEqual(1);
    });
  });

  it('reattaches to a collect that was already running', async () => {
    const fixture = await render(CORPUS, SEED, {
      jobs: [
        {
          id: 'C9',
          kind: 'harvest-collect',
          status: 'running',
          started_at: '2026-09-05T21:00:00+00:00',
          line_count: 0,
          ok: null,
          stoppable: true,
        },
      ],
    });

    expect(fixture.componentInstance.collecting()).toBe(true);
  });
});