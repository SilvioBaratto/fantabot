import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { BackfillCandidates } from '../../core/models/backfill';
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

  const CANDIDATES: BackfillCandidates = {
    home: '/Users/me/.fantabot/aste_live',
    exists: true,
    error: null,
    logs: [
      {
        name: 'events_2026-08-26.jsonl',
        bytes: 82384171,
        mtime: '2026-08-27T04:22:00+00:00',
        live: false,
      },
      { name: 'live.jsonl', bytes: 1312911024, mtime: '2026-09-07T13:38:00+00:00', live: true },
    ],
    seeds: [
      { name: 'seed.json', rows: 1705, mtime: '2026-09-01T15:33:00+00:00' },
      { name: 'seed_2026-08-26.json', rows: 66, mtime: '2026-08-26T23:58:00+00:00' },
    ],
  };

  async function render(
    body: Corpus = CORPUS,
    seed: SeedPanel = SEED,
    jobs: JobList = NO_JOBS,
    candidates: BackfillCandidates = CANDIDATES,
  ) {
    const fixture = TestBed.createComponent(HarvestComponent);
    fixture.detectChanges(); // ngOnInit fires the reads
    httpMock.expectOne(`${environment.apiUrl}harvest/corpus`).flush(body);
    httpMock.expectOne(`${environment.apiUrl}harvest/seed`).flush(seed);
    httpMock.expectOne(`${environment.apiUrl}harvest/backfill/candidates`).flush(candidates);
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
    httpMock.expectOne(`${environment.apiUrl}harvest/backfill/candidates`).flush(CANDIDATES);
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
    const fixture = await render(CORPUS, {
      ...SEED,
      exists: false,
      rows: 0,
      mtime: null,
      formats: {},
    });

    expect(fixture.nativeElement.textContent).toContain('No seed yet');
  });

  it('starts a scan and streams its lines', async () => {
    const fixture = await render();

    fixture.componentInstance.runScan();
    fixture.detectChanges();
    httpMock.expectOne(`${environment.apiUrl}actions/harvest-scan`).flush({ job_id: 'j1' });
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
    const scan = root.querySelector('[data-panel="seed"]') as HTMLElement;
    const collector = root.querySelector('[data-panel="collector"]') as HTMLElement;

    expect(loader.querySelector('mat-select')).not.toBeNull();
    // Scoped to the two panels the rule is about rather than counting the page. A census
    // read as "one format selector exists" and meant "no card has been added since" —
    // T22's backfill has a format for the loader's own reason, a parameter of the *read*,
    // and tripped it while breaking nothing the rule protects.
    expect(scan.querySelector('mat-select')).toBeNull();
    expect(collector.querySelector('mat-select')).toBeNull();
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
      .flush(
        { detail: 'pool is 1000 and the seed holds 1705 auction(s)' },
        { status: 400, statusText: 'Bad Request' },
      );
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

    const panel = fixture.nativeElement.querySelector('[data-panel="collector"]') as HTMLElement;

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

  // -- the backfill picker (T22) -------------------------------------------------------

  const backfillCard = (fixture: { nativeElement: HTMLElement }) =>
    fixture.nativeElement.querySelector('[data-panel="backfill"]') as HTMLElement;

  const control = (fixture: { nativeElement: HTMLElement }, name: string) =>
    backfillCard(fixture).querySelector(`[data-control="${name}"]`) as HTMLButtonElement;

  /**
   * Open one of the card's selects and return its options, which live in the CDK overlay
   * attached to `document.body` — never inside the card. A `querySelector` on the card
   * finds nothing and would let every one of these assertions pass on an empty picker.
   */
  async function options(
    fixture: {
      nativeElement: HTMLElement;
      detectChanges: () => void;
      whenStable: () => Promise<unknown>;
    },
    field: string,
  ): Promise<HTMLElement[]> {
    const trigger = backfillCard(fixture).querySelector(
      `.${field} .mat-mdc-select-trigger`,
    ) as HTMLElement;
    trigger.click();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return [...document.querySelectorAll('mat-option')] as HTMLElement[];
  }

  /** The default the picker lands on: the newest *recorded* log, not the live zone. */
  const DEFAULT_CHOICE = 'events_2026-08-26.jsonl|seed.json|mantra';

  it('offers every recorded log the home holds', async () => {
    // The first place the app has wanted a file path, and the reason it is a picker: a
    // typed harvest path *creates* what it cannot find, which is how the real home came
    // to hold three stray landing zones each with its own checkpoint.
    const fixture = await render();

    const names = (await options(fixture, 'field-log')).map((el) => el.getAttribute('data-log'));

    expect(names).toEqual(['events_2026-08-26.jsonl', 'live.jsonl']);
  });

  it('marks the live landing zone as the live one', async () => {
    // Offered, never hidden — a backfill reads it and every write is an upsert, so the
    // offset that belongs to `harvest load` has nothing to fear. But the operator has to
    // know which name is the file a collect may be appending to right now.
    const fixture = await render();

    const shown = await options(fixture, 'field-log');
    const live = shown.find((el) => el.getAttribute('data-log') === 'live.jsonl');
    const recorded = shown.find((el) => el.getAttribute('data-log') === 'events_2026-08-26.jsonl');

    // `landing zone`, not `live`: the live option's own filename is `live.jsonl`, so an
    // assertion on that word passes on the name and says nothing about the label. The
    // battery caught it by deleting the label and staying green.
    expect(live?.textContent?.toLowerCase()).toContain('landing zone');
    expect(recorded?.textContent?.toLowerCase()).not.toContain('landing zone');
  });

  it("shows each seed's auction count beside its name", async () => {
    // The only thing that tells today's seed from a recorded evening's before the run.
    // Getting the pair wrong is silent: every auction the seed does not describe is
    // dropped as `unknown auction` and the run still reports success.
    const fixture = await render();

    const shown = await options(fixture, 'field-seed');
    const byName = new Map(shown.map((el) => [el.getAttribute('data-seed'), el.textContent ?? '']));

    expect([...byName.keys()]).toEqual(['seed.json', 'seed_2026-08-26.json']);
    expect(byName.get('seed.json')).toContain('1,705');
    expect(byName.get('seed_2026-08-26.json')).toContain('66');
  });

  it('lands on the newest recorded log rather than the live landing zone', async () => {
    // The live zone is what `harvest load --follow` is already carrying, so a backfill of
    // it is legal and almost never what was meant. A default that pointed there would make
    // the one destructive-looking choice the easiest click on the card.
    const fixture = await render();

    expect(fixture.componentInstance.backfillLog()).toBe('events_2026-08-26.jsonl');
    expect(fixture.componentInstance.backfillSeed()).toBe('seed.json');
  });

  it('offers no write until a dry run has come back for this exact triple', async () => {
    const fixture = await render();
    const component = fixture.componentInstance;

    expect(control(fixture, 'backfill-write').disabled).toBe(true);

    component.backfillDryRunDone.set(DEFAULT_CHOICE);
    fixture.detectChanges();

    expect(control(fixture, 'backfill-write').disabled).toBe(false);
  });

  it('records the receipt only when a dry run actually finished clean', async () => {
    // The line the rest of the gate hangs on, and the only one that needs the 1500 ms poll
    // this suite otherwise never drives. Setting the signal by hand in the tests above
    // proves the *comparison*; without this, deleting the assignment leaves them all green.
    vi.useFakeTimers();
    try {
      const fixture = await render();

      control(fixture, 'backfill-dry-run').click();
      httpMock.expectOne(`${environment.apiUrl}harvest/backfill`).flush({ job_id: 'B7' });
      await vi.advanceTimersByTimeAsync(1600);
      httpMock.expectOne(`${environment.apiUrl}jobs/B7?since=0`).flush({
        id: 'B7',
        status: 'done',
        ok: true,
        lines: ['auctions 66 · events 328 from 900 states · assignments 18'],
        error: null,
      });
      await vi.advanceTimersByTimeAsync(0);

      expect(fixture.componentInstance.backfillDryRunDone()).toBe(DEFAULT_CHOICE);
    } finally {
      vi.useRealTimers();
    }
  });

  it('records no receipt when the dry run failed', async () => {
    // A child that died holding an exception has reported nothing about the pair, so the
    // write must stay withdrawn. `ok: false` is the run the operator most needs stopped.
    vi.useFakeTimers();
    try {
      const fixture = await render();

      control(fixture, 'backfill-dry-run').click();
      httpMock.expectOne(`${environment.apiUrl}harvest/backfill`).flush({ job_id: 'B8' });
      await vi.advanceTimersByTimeAsync(1600);
      httpMock.expectOne(`${environment.apiUrl}jobs/B8?since=0`).flush({
        id: 'B8',
        status: 'error',
        ok: false,
        lines: ['exited 2'],
        error: 'InvalidBackfill: seed file not found',
      });
      await vi.advanceTimersByTimeAsync(0);

      expect(fixture.componentInstance.backfillDryRunDone()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('withdraws the write when any part of the triple changes', async () => {
    // What the sequencing is for. A dry run proves *one* (log, seed, format); the number it
    // exists to show — how many auctions the seed failed to describe — says nothing at all
    // about a different one.
    const fixture = await render();
    const component = fixture.componentInstance;
    component.backfillDryRunDone.set(DEFAULT_CHOICE);
    fixture.detectChanges();

    component.backfillSeed.set('seed_2026-08-26.json');
    fixture.detectChanges();
    expect(control(fixture, 'backfill-write').disabled).toBe(true);

    component.backfillDryRunDone.set('events_2026-08-26.jsonl|seed_2026-08-26.json|mantra');
    fixture.detectChanges();
    expect(control(fixture, 'backfill-write').disabled).toBe(false);

    component.backfillFormat.set('classic');
    fixture.detectChanges();
    expect(control(fixture, 'backfill-write').disabled).toBe(true);
  });

  it('sends the chosen triple with dry_run true', async () => {
    const fixture = await render();

    control(fixture, 'backfill-dry-run').click();
    const request = httpMock.expectOne(`${environment.apiUrl}harvest/backfill`);

    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      log: 'events_2026-08-26.jsonl',
      seed: 'seed.json',
      asta_type: 'mantra',
      dry_run: true,
    });
    request.flush({ job_id: 'B1' });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.backfillRunning()).toBe(true);
  });

  it('sends dry_run false on the write, and spends the receipt doing so', async () => {
    // The receipt cannot be spent twice: by the time a write finishes the corpus has
    // moved, and the next write is a different question.
    const fixture = await render();
    fixture.componentInstance.backfillDryRunDone.set(DEFAULT_CHOICE);
    fixture.detectChanges();

    control(fixture, 'backfill-write').click();
    const request = httpMock.expectOne(`${environment.apiUrl}harvest/backfill`);

    expect(request.request.body.dry_run).toBe(false);
    expect(request.request.body.log).toBe('events_2026-08-26.jsonl');
    request.flush({ job_id: 'B2' });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.componentInstance.backfillDryRunDone()).toBeNull();
  });

  it('starts a dry run by withdrawing whatever receipt was standing', async () => {
    // Otherwise a dry run that fails leaves the previous run's receipt in place and the
    // write stays offered against a pair nothing has just reported on.
    const fixture = await render();
    fixture.componentInstance.backfillDryRunDone.set(DEFAULT_CHOICE);
    fixture.detectChanges();

    control(fixture, 'backfill-dry-run').click();

    expect(fixture.componentInstance.backfillDryRunDone()).toBeNull();
    httpMock.expectOne(`${environment.apiUrl}harvest/backfill`).flush({ job_id: 'B3' });
    await fixture.whenStable();
  });

  it("renders the server's refusal verbatim rather than a generic failure", async () => {
    // The refusal names the file and lists what is available, which is the whole value of
    // it — a generic "could not start" throws away the only thing the operator needs.
    const fixture = await render();

    control(fixture, 'backfill-dry-run').click();
    httpMock
      .expectOne(`${environment.apiUrl}harvest/backfill`)
      .flush(
        { detail: "'assignments_2026-08-26.jsonl' is not a candidate. Available: live.jsonl" },
        { status: 400, statusText: 'Bad Request' },
      );
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(backfillCard(fixture).textContent).toContain('is not a candidate');
    expect(control(fixture, 'backfill-write').disabled).toBe(true);
  });

  it('says what to run when there is no harvest home', async () => {
    // Not an empty select: "nothing to load" and "the home is not there" send the operator
    // to two different places, and only one of them names a command.
    const fixture = await render(CORPUS, SEED, NO_JOBS, {
      home: '/Users/me/.fantabot/aste_live',
      exists: false,
      error: null,
      logs: [],
      seeds: [],
    });

    expect(backfillCard(fixture).textContent).toContain('harvest adopt');
    expect(control(fixture, 'backfill-dry-run').disabled).toBe(true);
  });

  it('distinguishes a home that cannot be read from one that is not there', async () => {
    const fixture = await render(CORPUS, SEED, NO_JOBS, {
      home: '/Users/me/.fantabot/aste_live',
      exists: true,
      error: 'PermissionError',
      logs: [],
      seeds: [],
    });

    expect(backfillCard(fixture).textContent).toContain('PermissionError');
    expect(backfillCard(fixture).textContent).not.toContain('harvest adopt');
  });

  it('says there is nothing to re-read when the home holds no recorded log', async () => {
    const fixture = await render(CORPUS, SEED, NO_JOBS, {
      home: '/Users/me/.fantabot/aste_live',
      exists: true,
      error: null,
      logs: [],
      seeds: [{ name: 'seed.json', rows: 1705, mtime: null }],
    });

    expect(backfillCard(fixture).textContent).toContain('no recorded collector log');
    expect(control(fixture, 'backfill-dry-run').disabled).toBe(true);
  });

  it('reattaches to a backfill that was already running', async () => {
    // The child is a subprocess and outlives the page. A refresh mid-run must reattach
    // rather than re-enable a button that would start a second one over the same file.
    const fixture = await render(CORPUS, SEED, {
      jobs: [
        {
          id: 'B9',
          kind: 'harvest-backfill',
          status: 'running',
          started_at: '2026-09-20T10:00:00+00:00',
          line_count: 0,
          ok: null,
          stoppable: true,
        },
      ],
    });

    expect(fixture.componentInstance.backfillRunning()).toBe(true);
    // No receipt is inferred on reattach: `GET /jobs` does not say whether the running
    // child carries `--dry-run`, and guessing would offer the write on a run whose mode
    // this page does not know.
    expect(fixture.componentInstance.backfillDryRunDone()).toBeNull();
  });

  it('asks the server to stop the backfill rather than forgetting it locally', async () => {
    const fixture = await render();
    control(fixture, 'backfill-dry-run').click();
    httpMock.expectOne(`${environment.apiUrl}harvest/backfill`).flush({ job_id: 'B4' });
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.stopBackfill();

    expect(httpMock.expectOne(`${environment.apiUrl}jobs/B4/stop`).request.method).toBe('POST');
  });
});
