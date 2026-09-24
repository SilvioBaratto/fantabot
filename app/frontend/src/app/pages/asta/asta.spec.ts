import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { AstaPlan } from '../../core/models/asta-plan';
import { Exclusion, Exclusions } from '../../core/models/exclusion';
import { JobSummary } from '../../core/models/job';
import { JournalPage, JournalRow } from '../../core/models/journal';
import { BidStarted, RoomCheck } from '../../core/models/room';
import { WINDOW_SIZE_QUERIES, WindowSizeClass } from '../../core/window-size-class';
import { AstaComponent } from './asta';

describe('AstaComponent', () => {
  let httpMock: HttpTestingController;

  /**
   * Make `matchMedia` report a match for exactly one M3 query, the same seam
   * `WindowSizeClassService` reads in production. jsdom evaluates no CSS and reports no
   * width, so with no stub nothing matches and the service falls back to `compact` — which
   * is what every test below that does not call this one is exercising.
   *
   * It has to run before the component is created: the service is injected in a field
   * initializer and reads the queries once, on construction.
   */
  function stubSizeClass(size: WindowSizeClass): void {
    const target = WINDOW_SIZE_QUERIES[size];
    vi.spyOn(window, 'matchMedia').mockImplementation(
      (query: string) =>
        ({
          matches: query === target,
          media: query,
          onchange: null,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    );
  }

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AstaComponent],
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

  afterEach(() => {
    vi.restoreAllMocks();
    // The attached run's status heartbeat, drained rather than asserted.
    //
    // Since `attach` starts it, every live-room test has one of these pending on the tail's
    // own cadence — a child that ends by itself has to be noticed, and asking is the only
    // way to notice. No test below is *about* the heartbeat, so draining it here keeps
    // `verify()` meaningful for the requests that are under test instead of making thirty
    // tests flush a poll none of them asserts on. The two that **are** about it
    // (`detaches only once the server says the job has ended`, `stops the watch and stops
    // tailing once the job has actually ended`) match it with `expectOne` first, so they
    // consume theirs before this runs and still fail if it stops being sent.
    for (const open of httpMock.match((r) => /jobs\/[^/]+$/.test(r.url))) {
      // `cancelled` first: `detach()` clears the job id, the tail's `takeWhile` completes,
      // and RxJS unsubscribes the poll that was already in flight. `match()` still returns
      // it and flushing it throws "Cannot flush a cancelled request" — which is an error in
      // `afterEach`, and a failing `afterEach` leaves the TestBed instantiated, so the next
      // three files die with "Cannot configure the test module". That is the standing
      // hazard, reached from the cleanup written to avoid it.
      if (!open.cancelled) {
        open.flush({ id: 'drained', status: 'running', lines: [], ok: null, error: null });
      }
    }
    httpMock.verify();
  });

  /**
   * The second request `ngOnInit` fires, flushed by every test that is not about
   * exclusions.
   *
   * Not optional book-keeping: an outstanding request fails `httpMock.verify()` in
   * `afterEach`, and a failing `afterEach` leaves the TestBed instantiated — one page's
   * extra `ngOnInit` request once took down `prices`, `app` and `toast` with "Cannot
   * configure the test module", 15 failures across 3 files and none of them the page.
   */
  function flushExclusions(body: Partial<Exclusions> = {}): void {
    httpMock
      .expectOne(`${environment.apiUrl}db/exclusions`)
      .flush({ exclusions: [], error: null, ...body });
  }

  /**
   * The third request `ngOnInit` fires, and the one that reattaches the live room.
   *
   * A listing that cannot be read is not an error worth showing — harvest's reasoning —
   * but it is still a request, and an outstanding one fails `httpMock.verify()`.
   */
  function flushJobs(jobs: JobSummary[] = []): void {
    httpMock.expectOne(`${environment.apiUrl}jobs`).flush({ jobs });
  }

  function overview(id: number) {
    return {
      league_id: id,
      league_name: 'Legamiallerotaie2',
      captured_at: null,
      matchday: null,
      budget: 500,
      roster_size: 30,
      roster_provenance: "read from the lega's last sync",
      min_roles: null,
      max_roles: null,
      modules: null,
      bench_size: null,
      team_count: 8,
    };
  }

  function plan(over: Partial<AstaPlan> = {}): AstaPlan {
    return {
      found: true,
      outcome: 'planned',
      reason: null,
      listone: 'mantra',
      roster_size: 30,
      roster_provenance: "read from the lega's last sync",
      total_cost: 500,
      objective: 1897,
      budget: 500,
      lam: 0,
      owned: [],
      callable_pool: 529,
      players: [
        {
          player_id: '1',
          nome: 'Svilar',
          price: 20,
          walk_away: 34,
          walk_away_provenance:
            're-solved: the most this rosa would pay before it is no better off',
        },
      ],
      fallbacks: [],
      ...over,
    };
  }

  /** A component with one lega selected and `body` already flushed as its plan. */
  async function readyWithPlan(body: AstaPlan) {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();
    flushExclusions();
    flushJobs();
    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();
    httpMock.expectOne((r) => r.url.includes('asta/plan')).flush(body);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture;
  }

  it('auto-selects the first lega and renders its plan', async () => {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();
    flushExclusions();
    flushJobs();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock.expectOne((r) => r.url.includes('asta/plan')).flush(plan());
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Svilar');
    // Sentence case, per the M3 content rules; this label was lower-case `objective`.
    expect(text).toContain('Objective');
  });

  it('says what the plan was built on', async () => {
    // The page showed a number and none of the inputs behind it, and those inputs
    // differed from the command's in ten places (the archived parity-phase spec, §11.1).
    const fixture = await readyWithPlan(plan({ lam: 0.3, owned: ['9'], callable_pool: 529 }));

    const text = fixture.nativeElement.textContent as string;
    // Sentence case, per the M3 content rules; these labels were lower-case.
    expect(text).toContain('Risk (lam)');
    expect(text).toContain('0.3');
    expect(text).toContain('Callable pool');
    expect(text).toContain('529');
  });

  it('renders an unnarrowed pool as unnarrowed, not as zero', async () => {
    // `null` means the listone was unreachable and the plan ran over the whole pool. A
    // pool narrowed to nothing is a different fact and would be a plan over nobody.
    const fixture = await readyWithPlan(plan({ callable_pool: null }));

    expect(fixture.nativeElement.textContent).toContain('Not narrowed');
  });

  it('shows the next-best plans the command prints', async () => {
    // A single optimal rosa reads as a prescription and is not one: the evening takes
    // players off the board.
    const fixture = await readyWithPlan(
      plan({
        fallbacks: [
          { total_cost: 498, objective: 1880 },
          { total_cost: 494, objective: 1871 },
        ],
      }),
    );

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Next best');
    // Through the `number` pipe, so the grouping separator is part of what is rendered.
    expect(text).toContain('1,880');
    expect(text).toContain('1,871');
  });

  it("shows the endpoint's own reason when there is no plan", async () => {
    // "Run `news fetch` first" and "the database is down" used to be the same screen.
    const fixture = await readyWithPlan(
      plan({
        found: false,
        outcome: 'no_sentiment',
        reason:
          'sentiment is on but there are no rows in the database. Run `fantabot news fetch --write`.',
        players: [],
      }),
    );

    const text = fixture.nativeElement.textContent as string;
    // The heading is chosen by the outcome, the remedy comes from the reason. One label
    // over five failures is what T31 records the cost of.
    expect(text).toContain('The news feed is empty');
    expect(text).toContain('news fetch');
  });

  /**
   * The room check. Its own section, and its own outcomes: T31 (§3.3 of the app's
   * maintainer-local `BACKLOG.md`) records
   * what one label over four failures costs, so the screen must render five different
   * answers differently. It is also the only surface that tells the operator whether the
   * stored FantaLab credential still works.
   */
  describe('room check', () => {
    async function ready() {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    async function check(fixture: Awaited<ReturnType<typeof ready>>, body: Partial<RoomCheck>) {
      fixture.componentInstance.setRoomUrl('https://app.fantalab.it/asta?asta=abc');
      fixture.componentInstance.checkRoom();
      httpMock.expectOne((r) => r.url.includes('asta/room')).flush(body);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture.nativeElement.textContent as string;
    }

    it('renders the six facts, with the roster band beside its provenance', async () => {
      const text = await check(await ready(), {
        outcome: 'resolved',
        reason: '',
        fantaleague_id: 'abc',
        shard: 4,
        asta_type: 'mantra',
        asta_mode: 'chiamata',
        raise_mode: 'free',
        num_teams: 8,
        num_credits: 500,
        seat_team_id: 'team-ours',
        seat_team_name: 'Legamiallerotaie',
        seat_user_id: 'USER-9',
        roster_size: 25,
        roster_provenance: 'read from the room',
      });

      expect(text).toContain('Legamiallerotaie');
      expect(text).toContain('chiamata');
      expect(text).toContain('free');
      expect(text).toContain('25');
      expect(text).toContain('read from the room');
    });

    it('shows a refusal in the rooms own words', async () => {
      const text = await check(await ready(), {
        outcome: 'refused',
        reason: 'we hold no seat in this room. Claim one in the browser first',
        fantaleague_id: 'abc',
        roster_provenance: '',
      });

      expect(text).toContain('no seat in this room');
    });

    it('tells a dead credential apart from a room that refused us', async () => {
      // The distinction §3.4 is about: both are "you cannot bid tonight", and only one
      // of them is fixed by going back to the room.
      const text = await check(await ready(), {
        outcome: 'no_credential',
        reason: 'this row was encrypted with key aa695c77, but FANTABOT_ENCRYPTION_KEY is ef341176',
        fantaleague_id: 'abc',
        roster_provenance: '',
      });

      expect(text).toContain('aa695c77');
      expect(text.toLowerCase()).toContain('fantalab');
    });

    it('says a bad link is a bad link, not a failure', async () => {
      const text = await check(await ready(), {
        outcome: 'bad_link',
        reason: 'that is an invitation link (abc), not a room link.',
        fantaleague_id: null,
        roster_provenance: '',
      });

      expect(text).toContain('invitation link');
    });

    it('offers a bid control that is disarmed until it is told otherwise', async () => {
      // **Replaced at 3.9c, not deleted — T21's rule, in the commit that earns it.** This
      // asserted that no button on the page could bid, which was true until the page could.
      // Absence is the weaker property and it stops being checkable the moment the feature
      // lands; what survives is the arming contract: the control exists, and it is off.
      //
      // A page that loaded with it on is a lock nobody turned, which is the failure mode
      // `application/arming` is written against — a reload, a restored session, or a tab
      // left open overnight, none of which may carry an arming decision forward.
      const fixture = await ready();
      await check(fixture, {
        outcome: 'resolved',
        reason: '',
        fantaleague_id: 'abc',
        shard: 4,
        asta_type: 'mantra',
        asta_mode: 'chiamata',
        raise_mode: 'free',
        num_teams: 8,
        num_credits: 500,
        seat_team_id: 'team-ours',
        seat_team_name: 'Legamiallerotaie',
        seat_user_id: 'USER-9',
        roster_size: 25,
        roster_provenance: 'read from the room',
      });

      const labels = Array.from(fixture.nativeElement.querySelectorAll('button')).map((b) =>
        ((b as HTMLButtonElement).textContent ?? '').toLowerCase(),
      );
      expect(labels.some((l) => /bid/.test(l))).toBe(true);

      const arm = fixture.nativeElement.querySelector(
        '[data-testid="arm-checkbox"] input',
      ) as HTMLInputElement;
      expect(arm).not.toBeNull();
      expect(arm.checked).toBe(false);
      expect(fixture.componentInstance.armRequested()).toBe(false);
      // Both, because they are two claims: the signal is what the request carries, and the
      // box is what the operator reads. A page whose control and state disagreed would arm
      // a run the screen says is a rehearsal.
    });
  });

  /**
   * The room journal. `data/room_journal.jsonl` holds 5,192 rows from 2026-09-01 and
   * nothing anywhere read it: the audit that found all three bidder defects was done by
   * hand against the file. It is the CLI's record, and the screen has to say so — the app
   * never bids, so a journal on an app page is evidence, not a log of what it did.
   */
  describe('room journal', () => {
    async function ready() {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    function page(over: Partial<JournalPage> = {}): JournalPage {
      return {
        ok: true,
        path: '/Volumes/External SSD/fantabot/data/room_journal.jsonl',
        exists: true,
        total: 5192,
        skipped: 0,
        offset: 0,
        limit: 100,
        // Zero in page mode, and that is the answer: a page does not tail.
        next_index: 0,
        rows: [],
        error: null,
        ...over,
      };
    }

    function journalRow(over: Partial<JournalRow> = {}): JournalRow {
      return {
        index: 5192,
        at_ms: 1788304436211,
        node: 'auction',
        lot: 'b894b38e',
        name: 'Holm',
        price: 1,
        walk_away: null,
        provenance: null,
        decision: 'hold',
        reason: null,
        credits_left: 29,
        max_cap: 27,
        owned_count: 27,
        bargain_spent: null,
        bargain_allowance: null,
        error: null,
        cycle_ms: null,
        ...over,
      };
    }

    it('tells a skipped poll from a crash', async () => {
      // Both carry two or three keys, so until `error` was read they rendered as the same
      // row of nulls — and distinguishing them is the whole purpose of writing an error
      // row at all.
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();

      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(
          page({
            rows: [
              journalRow({
                index: 5192,
                name: null,
                lot: null,
                price: null,
                credits_left: null,
                max_cap: null,
                owned_count: null,
                decision: 'error',
                error: 'ReadTimeout',
              }),
              journalRow({
                index: 5191,
                name: null,
                lot: null,
                price: null,
                credits_left: null,
                max_cap: null,
                owned_count: null,
                decision: 'waiting',
              }),
            ],
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('ReadTimeout');
      expect(text).toContain('waiting');
    });

    it('shows what the evening has already spent off-plan', async () => {
      // The aggregate cap. It has been written since `44cfe89` and read by nothing: an
      // operator who cannot see it only learns it exists by not understanding a held bid.
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();

      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ rows: [journalRow({ bargain_spent: 37, bargain_allowance: 50 })] }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('37/50');
    });

    it('costs nothing until it is opened', async () => {
      // 1.6 MB parsed on every visit to a page whose subject is the plan would be a cost
      // nobody asked for. The journal is a post-mortem.
      const fixture = await ready();
      httpMock.expectNone((r) => r.url.includes('asta/journal'));
      expect(fixture.nativeElement.textContent).toContain('Room journal');
    });

    it('renders newest-first and says whose record it is', async () => {
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();

      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(
          page({
            rows: [
              journalRow({ index: 5192, name: 'Holm', decision: 'hold' }),
              journalRow({ index: 5191, name: 'Zaccagni', decision: 'bid', walk_away: 41 }),
            ],
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Holm');
      expect(text).toContain('Zaccagni');
      expect(text).toContain('5192');
      // The label, not a docstring: the record is the run's own — a CLI command, whichever
      // surface started it — and never something the app process wrote.
      expect(text).toContain('written by the run itself');
      expect(text).toContain('fantabot asta bid');
      // It used to say "The app never bids", which stopped being true when Bid in room shipped.
      expect(text).not.toContain('The app never bids');
    });

    it('asks for the next page by offset, and never re-asks for the first', async () => {
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ rows: [journalRow()] }));
      fixture.detectChanges();
      await fixture.whenStable();

      fixture.componentInstance.nextPage();
      const next = httpMock.expectOne((r) => r.url.includes('asta/journal'));
      expect(next.request.params.get('offset')).toBe('100');
      next.flush(page({ offset: 100, rows: [journalRow({ index: 5092 })] }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('5092');
    });

    it('keeps the pager focusable while its page is in flight', async () => {
      // Both buttons carried `|| journalLoading()`, and `loadJournal` sets that flag
      // synchronously — so the browser disabled the button the operator had just pressed
      // and focus fell to `<body>` on every page turn. The in-flight guard belongs in
      // `loadJournal`, which is where a second request is actually refused.
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ rows: [journalRow()] }));
      fixture.detectChanges();
      await fixture.whenStable();

      const host = fixture.nativeElement as HTMLElement;
      const older = Array.from(host.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === 'Older',
      );
      expect(older).toBeDefined();
      older?.focus();
      older?.click();
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.journalLoading()).toBe(true);
      expect(older?.disabled).toBe(false);
      expect(document.activeElement).toBe(older);

      // And the guard still holds: a second click while one is in flight asks for nothing.
      older?.click();
      const inFlight = httpMock.match((r) => r.url.includes('asta/journal'));
      expect(inFlight.length).toBe(1);
      inFlight[0].flush(page({ offset: 100, rows: [journalRow({ index: 5092 })] }));
      fixture.detectChanges();
      await fixture.whenStable();
    });

    it('moves focus into the rows when a page turn reaches the last page', async () => {
      // Reaching a bound is the one thing that still takes the pressed button away, and a
      // disabled element cannot hold focus. The rows it turned to are where focus belongs.
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ total: 101, limit: 100, rows: [journalRow()] }));
      fixture.detectChanges();
      await fixture.whenStable();

      const host = fixture.nativeElement as HTMLElement;
      const older = Array.from(host.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === 'Older',
      );
      older?.focus();
      older?.click();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ total: 101, limit: 100, offset: 100, rows: [journalRow({ index: 5092 })] }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.hasNextPage()).toBe(false);
      expect(older?.disabled).toBe(true);
      expect(document.activeElement).not.toBe(document.body);
      expect((document.activeElement as HTMLElement).id).toBe('journal-body');
    });

    it('names the path and the commands when there is no journal yet', async () => {
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ exists: false, total: 0, rows: [] }));
      fixture.detectChanges();
      await fixture.whenStable();

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('No journal yet');
      // Where it looked, because `fantabot_data_dir` is relative to whatever working
      // directory the launcher was started in.
      expect(text).toContain('data/room_journal.jsonl');
      expect(text).toContain('fantabot asta room');
    });

    it('reports a torn line rather than hiding it', async () => {
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ total: 5191, skipped: 1, rows: [journalRow()] }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('1 line');
    });

    /**
     * Eight columns need roughly 900px, which is more than this page's pane has below 840px
     * once the shell's docked rail is out of the window. M3's answer to a table that will
     * not fit is a different layout, never a horizontal scroll of eight columns
     * (`layout/breakpoints.md:67`), so below 840px each row is a card — and every label the
     * header row would have carried travels with it.
     */
    it('draws the journal as cards below 840px, with every column still labelled', async () => {
      stubSizeClass('medium');
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(
          page({
            rows: [
              journalRow({
                name: 'Holm',
                walk_away: null,
                bargain_spent: 37,
                bargain_allowance: 50,
              }),
            ],
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.querySelector('table')).toBeNull();
      expect(fixture.nativeElement.querySelectorAll('.journal-card').length).toBe(1);

      const text = fixture.nativeElement.textContent as string;
      for (const label of ['Price', 'Walk-away', 'Decision', 'Cap', 'Left', 'Bargain']) {
        expect(text).toContain(label);
      }
      expect(text).toContain('#5192');
      expect(text).toContain('37/50');
      // Still a null and never a zero: 4,501 of the 5,192 recorded rows look like this.
      expect(text).toContain('null');
    });

    it('draws the journal as a table from 840px up', async () => {
      stubSizeClass('expanded');
      const fixture = await ready();
      fixture.componentInstance.toggleJournal();
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(page({ rows: [journalRow({ name: 'Holm' })] }));
      fixture.detectChanges();
      await fixture.whenStable();

      const table = fixture.nativeElement.querySelector('table.journal-table') as HTMLTableElement;
      expect(table).not.toBeNull();
      expect(
        Array.from(table.querySelectorAll('thead th')).map((h) => h.textContent?.trim()),
      ).toEqual(['#', 'Lot', 'Price', 'Walk-away', 'Decision', 'Cap', 'Left', 'Bargain']);
      expect(table.querySelectorAll('tbody tr').length).toBe(1);
      expect(table.textContent).toContain('Holm');
    });
  });

  /**
   * The targets. Three columns fit a phone; the walk-away's provenance sentence does not,
   * so at compact the row becomes a card and the two figures get their labels back.
   */
  describe('targets, per size class', () => {
    it('draws the targets as cards at compact, not a table', async () => {
      stubSizeClass('compact');
      const fixture = await readyWithPlan(plan());

      expect(fixture.nativeElement.querySelector('table')).toBeNull();
      expect(fixture.nativeElement.querySelectorAll('.target-card').length).toBe(1);

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Svilar');
      expect(text).toContain('Corpus price');
      expect(text).toContain('Walk-away');
      expect(text).toContain('34');
      expect(text).toContain('Re-solved');
    });

    it('draws the targets as a table from 600px up', async () => {
      stubSizeClass('medium');
      const fixture = await readyWithPlan(plan());

      const table = fixture.nativeElement.querySelector('table.targets-table') as HTMLTableElement;
      expect(table).not.toBeNull();
      expect(
        Array.from(table.querySelectorAll('thead th')).map((h) => h.textContent?.trim()),
      ).toEqual(['Player', 'Corpus price', 'Walk-away']);
      expect(table.textContent).toContain('Svilar');
      // The provenance's kind stays beside the number it explains, never behind a hover.
      expect(table.textContent).toContain('Re-solved');
    });
  });

  it('shows a no-plan state when the pool is empty', async () => {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();
    flushExclusions();
    flushJobs();

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock
      .expectOne((r) => r.url.includes('asta/plan'))
      .flush(
        plan({
          found: false,
          outcome: 'empty_pool',
          listone: '',
          roster_size: 0,
          total_cost: 0,
          objective: 0,
          budget: 0,
          players: [],
        }),
      );
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No player pool');
  });

  it('shows the walk-away beside the corpus price, with its provenance', async () => {
    // The page had one number, labelled "Price", and it is the *market's* — the observed
    // mean clearing price. The one an operator bids against is the walk-away, and
    // `reservations` had zero call sites anywhere under app/ before this.
    const fixture = await readyWithPlan(plan());

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Corpus price');
    expect(text).toContain('Walk-away');
    expect(text).toContain('34');
    expect(text).toContain('Re-solved');
    // The explanation is on the page once, in the legend, not under every row.
    expect(text).toContain('the most this rosa would pay before it is no better off');
  });

  it('renders an unpriced walk-away as absent and never as zero', async () => {
    // Defect B2's shape: zero means "a substitute exists at this price" and is a real
    // answer; null means nobody priced it.
    const fixture = await readyWithPlan(
      plan({
        players: [
          {
            player_id: '1',
            nome: 'Svilar',
            price: 20,
            walk_away: null,
            walk_away_provenance: 'not priced: already owned',
          },
        ],
      }),
    );

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('already owned');
    expect(text).not.toContain('Walk-away0');
  });

  it('names an infeasible rosa as its own screen', async () => {
    // One of the five outcomes with a template branch and, until 1.18, no test. A branch
    // nobody renders in a test is a branch that survives a typo.
    const fixture = await readyWithPlan(
      plan({
        found: false,
        outcome: 'infeasible',
        reason: 'no schema can be seeded within budget',
        players: [],
      }),
    );

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('No legal rosa fits this budget');
    expect(text).toContain('no schema can be seeded');
  });

  it('says which lega has never been synced', async () => {
    const fixture = await readyWithPlan(
      plan({
        found: false,
        outcome: 'no_lega',
        reason: 'lega 4103937 has never been synced. Run `fantabot lega sync --write`.',
        players: [],
      }),
    );

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('This lega has never been synced');
    expect(text).toContain('lega sync');
  });

  it('shows where the roster band came from', async () => {
    // 2.1's third provenance. A band nobody declared and a band the lega stated are
    // different facts, and only one is worth planning on.
    const fixture = await readyWithPlan(
      plan({ roster_provenance: 'assumed — nothing was declared' }),
    );

    expect(fixture.nativeElement.textContent).toContain('assumed — nothing was declared');
  });
  /**
   * Excluded players — T24.
   *
   * The panel exists because an exclusion is invisible on every other screen: a plan
   * built without a player looks exactly like a plan built with one nobody wanted. So
   * the tests below are mostly about telling apart pairs of states that a careless
   * screen renders alike — an empty table from an unreadable one, a resolved name from
   * an unresolved one, a refusal from an unreachable API.
   */
  describe('excluded players', () => {
    function exclusion(over: Partial<Exclusion> = {}): Exclusion {
      return {
        player_id: 4344,
        nome: 'Leao',
        reason: 'left Serie A 2026-08-30',
        source: 'goal.com',
        ...over,
      };
    }

    /** A component with the leagues flushed empty and `body` flushed as the list. */
    async function ready(body: Partial<Exclusions> = {}) {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions(body);
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    it('shows each excluded player with the reason and the source', async () => {
      const fixture = await ready({ exclusions: [exclusion()] });

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Leao');
      expect(text).toContain('4344');
      expect(text).toContain('left Serie A 2026-08-30');
      // Beside the claim, not behind a hover: an exclusion with no provenance is
      // indistinguishable from a typo, and this one removes a player from every plan.
      expect(text).toContain('goal.com');
    });

    it('says an unresolved id is not scraped rather than showing a blank name', async () => {
      // `null` is a fact. The id is on no roster this database has scraped, so the row
      // is either a typo or a season to scrape — and it is doing nothing either way.
      const fixture = await ready({
        exclusions: [exclusion({ player_id: 999001, nome: null })],
      });

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Not scraped');
      expect(text).toContain('999001');
    });

    it('an empty table reads as every player being buyable', async () => {
      const fixture = await ready({ exclusions: [], error: null });

      expect(fixture.nativeElement.textContent).toContain('every player on the listone is buyable');
    });

    it('an unreadable list is a different screen from an empty one', async () => {
      // The `found=false` defect, restated. `[]` with no error and `[]` because Postgres
      // would not open need different remedies, so they need different screens.
      const fixture = await ready({
        exclusions: [],
        error: 'OperationalError: connection refused',
      });

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Could not read the exclusions');
      expect(text).toContain('OperationalError: connection refused');
      expect(text).not.toContain('every player on the listone is buyable');
    });

    it('posts the fields untouched and renders the list the server sent back', async () => {
      const fixture = await ready();
      const component = fixture.componentInstance;
      component.setExcludePlayerId(' 4344 ');
      component.setExcludeReason('  left Serie A  ');
      component.setExcludeSource(' goal.com ');
      component.exclude();

      const request = httpMock.expectOne(
        (r) => r.method === 'POST' && r.url === `${environment.apiUrl}db/exclusions`,
      );
      // The id is parsed because there is no number to send otherwise; everything else
      // crosses verbatim. What makes an exclusion valid is the server's decision, and a
      // trim here would be a second copy of it that refuses different things.
      expect(request.request.body).toEqual({
        player_id: 4344,
        reason: '  left Serie A  ',
        source: ' goal.com ',
      });

      request.flush({
        recorded: exclusion(),
        exclusions: [exclusion(), exclusion({ player_id: 999001, nome: null, reason: 'a guess' })],
        total: 2,
      });
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Leao');
      expect(text).toContain('a guess');
      // Cleared, so the next exclusion starts from nothing rather than from the last one.
      expect(component.excludePlayerId()).toBe('');
      expect(component.excludeReason()).toBe('');
    });

    it("shows the server's own refusal rather than composing one", async () => {
      // The sentence `fantabot db exclude` prints, because both surfaces carry
      // `clean_exclusion`'s wording. A locally composed message would let the page and
      // the terminal disagree about *why* something was refused.
      const fixture = await ready();
      fixture.componentInstance.setExcludePlayerId('4344');
      fixture.componentInstance.setExcludeReason('');
      fixture.componentInstance.exclude();

      httpMock
        .expectOne((r) => r.method === 'POST')
        .flush(
          { detail: 'an exclusion needs a reason. This removes the player from every plan.' },
          { status: 422, statusText: 'Unprocessable Entity' },
        );
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.nativeElement.textContent).toContain('an exclusion needs a reason');
      expect(fixture.componentInstance.excluding()).toBe(false);
    });

    it('does not send a request when the id is not a number', async () => {
      // `parseInt('')` is `NaN`, and a `NaN` in a JSON body serialises as `null` — which
      // the server rejects as a type error, not as the refusal the operator must read.
      const fixture = await ready();
      fixture.componentInstance.setExcludePlayerId('Leao');
      fixture.componentInstance.setExcludeReason('left Serie A');
      fixture.componentInstance.exclude();
      fixture.detectChanges();

      httpMock.expectNone((r) => r.method === 'POST');
      expect(fixture.nativeElement.textContent).toContain('a whole number');
    });

    it('says the API is unreachable rather than calling it a refusal', async () => {
      const fixture = await ready();
      fixture.componentInstance.setExcludePlayerId('4344');
      fixture.componentInstance.setExcludeReason('left Serie A');
      fixture.componentInstance.exclude();

      httpMock
        .expectOne((r) => r.method === 'POST')
        .error(new ProgressEvent('error'), { status: 0, statusText: 'Unknown Error' });
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(fixture.nativeElement.textContent).toContain('Could not reach the API');
    });

    /**
     * Removing one. The control exists because `fantabot db unexclude` does — SPEC.md
     * §8 Never #4 is about the app holding a power the CLI lacks, and the command
     * landed first. Until it did, a typo'd id was removed with `psql` and in no other
     * way, on a row that goes on dropping a player from every plan the bot builds.
     */
    describe('removing one', () => {
      it('the control on a row deletes that row and no other', async () => {
        const fixture = await ready({
          exclusions: [exclusion(), exclusion({ player_id: 999001, nome: null })],
        });

        // The second row, deliberately. A control wired to `exclusions()[0]` rather
        // than to its own row passes every assertion made against the first one, and
        // removes the wrong player for every operator who has more than one exclusion.
        const buttons: HTMLButtonElement[] = [
          ...fixture.nativeElement.querySelectorAll('.exclusion-remove'),
        ];
        expect(buttons.length).toBe(2);
        buttons[1].click();

        httpMock.expectNone(`${environment.apiUrl}db/exclusions/4344`);
        const request = httpMock.expectOne(`${environment.apiUrl}db/exclusions/999001`);
        expect(request.request.method).toBe('DELETE');
        request.flush({ removed: exclusion({ player_id: 999001 }), exclusions: [], total: 0 });
      });

      it('renders the list the server sent back, not this one minus a row', async () => {
        // The same argument the write takes: the table is what the page shows. A local
        // splice would disagree with it the moment anything else wrote a row.
        const fixture = await ready({ exclusions: [exclusion()] });

        fixture.componentInstance.unexclude(4344);
        httpMock.expectOne(`${environment.apiUrl}db/exclusions/4344`).flush({
          removed: exclusion(),
          exclusions: [exclusion({ player_id: 777, nome: 'Somebody Else', reason: 'retired' })],
          total: 1,
        });
        fixture.detectChanges();
        await fixture.whenStable();
        fixture.detectChanges();

        const text = fixture.nativeElement.textContent as string;
        expect(text).toContain('Somebody Else');
        expect(text).toContain('retired');
      });

      it('keeps the removed row on screen, because its reason is unrecoverable', async () => {
        // The id was typed and the name is on `players`; the sentence is held nowhere
        // else. `db unexclude` prints it for the same reason — it is what makes the
        // removal undoable by hand.
        const fixture = await ready({ exclusions: [exclusion()] });

        fixture.componentInstance.unexclude(4344);
        httpMock
          .expectOne(`${environment.apiUrl}db/exclusions/4344`)
          .flush({ removed: exclusion(), exclusions: [], total: 0 });
        fixture.detectChanges();
        await fixture.whenStable();
        fixture.detectChanges();

        const text = fixture.nativeElement.textContent as string;
        expect(text).toContain('left Serie A 2026-08-30');
        expect(text).toContain('every player on the listone is buyable');
      });

      it('shows the server sentence when the row was already gone', async () => {
        const fixture = await ready({ exclusions: [exclusion()] });

        fixture.componentInstance.unexclude(4344);
        httpMock
          .expectOne(`${environment.apiUrl}db/exclusions/4344`)
          .flush(
            { detail: 'no exclusion for id 4344 — nothing was removed.' },
            { status: 404, statusText: 'Not Found' },
          );
        fixture.detectChanges();
        await fixture.whenStable();
        fixture.detectChanges();

        expect(fixture.nativeElement.textContent).toContain('no exclusion for id 4344');
      });

      it('says the API is unreachable rather than calling it a refusal', async () => {
        const fixture = await ready({ exclusions: [exclusion()] });

        fixture.componentInstance.unexclude(4344);
        httpMock
          .expectOne(`${environment.apiUrl}db/exclusions/4344`)
          .error(new ProgressEvent('error'), { status: 0, statusText: 'Unknown Error' });
        fixture.detectChanges();
        await fixture.whenStable();
        fixture.detectChanges();

        expect(fixture.nativeElement.textContent).toContain('Could not reach the API');
      });
    });
  });
  /**
   * The live room view — T18's fourth surface, and the first one on this page that watches
   * something still happening.
   *
   * **The journal is the channel, not stdout.** `asta room` paints a Rich `Live`, which
   * renders to nobody on a pipe, so a supervised watch says what it is doing only by
   * appending a row per cycle to the file `GET /asta/journal?follow=1` tails. Everything
   * below therefore asserts against journal rows, never against job log lines.
   */
  describe('live room', () => {
    async function ready(jobs: JobSummary[] = []) {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      httpMock.expectOne(`${environment.apiUrl}jobs`).flush({ jobs });
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    function watching(over: Partial<JobSummary> = {}): JobSummary {
      return {
        id: 'W1',
        kind: 'asta-watch',
        status: 'running',
        started_at: '2026-09-20T19:31:00Z',
        line_count: 0,
        ok: null,
        stoppable: true,
        ...over,
      };
    }

    function tail(over: Partial<JournalPage> = {}): JournalPage {
      return {
        ok: true,
        path: '/Volumes/External SSD/fantabot/data/room_journal.jsonl',
        exists: true,
        total: 0,
        skipped: 0,
        offset: 0,
        limit: 100,
        next_index: 0,
        rows: [],
        error: null,
        ...over,
      };
    }

    function cycle(over: Partial<JournalRow> = {}): JournalRow {
      return {
        index: 1,
        at_ms: 1788304436211,
        node: 'auction',
        lot: 'b894b38e',
        name: 'Zaccagni',
        price: 40,
        walk_away: 44,
        provenance: 're-solved with this lot forced in',
        decision: 'bid',
        reason: null,
        credits_left: 312,
        max_cap: 96,
        owned_count: 7,
        bargain_spent: 12,
        bargain_allowance: 50,
        error: null,
        cycle_ms: 180.4,
        ...over,
      };
    }

    it('starts a watch from the pasted room link and tails the journal', async () => {
      const fixture = await ready();
      fixture.componentInstance.setRoomUrl('https://app.fantalab.it/asta?asta=abc');

      fixture.componentInstance.watchRoom();
      const started = httpMock.expectOne(`${environment.apiUrl}asta/room/watch`);
      // The link, and nothing else. No `arm`, and no number from the value model — both
      // are the child's, and a second copy of either here is what `asta_planner` exists
      // to prevent.
      expect(started.request.body).toEqual({ url: 'https://app.fantalab.it/asta?asta=abc' });
      started.flush({ job_id: 'W1' });
      fixture.detectChanges();
      await fixture.whenStable();

      // The first tail is immediate, not one poll late: an evening already under way has
      // rows to show now, and six seconds of a blank screen at 21:47 is the failure the
      // whole pane exists to prevent.
      const first = httpMock.expectOne((r) => r.url.includes('asta/journal'));
      expect(first.request.params.get('follow')).toBe('1');
      expect(first.request.params.get('since')).toBe('0');
      first.flush(tail({ rows: [cycle()], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.watchJobId()).toBe('W1');
      expect(fixture.componentInstance.liveRow()?.name).toBe('Zaccagni');
    });

    it('resumes from the position the server returned, never the one it asked for', async () => {
      // `next_index` is the last row *parsed*, which a torn line makes smaller than the
      // file's length. A viewer that counted its own rows would step over the cycle that
      // line belongs to and drop it from the evening's only record.
      vi.useFakeTimers();
      try {
        const fixture = await ready();
        fixture.componentInstance.setRoomUrl('abc');
        fixture.componentInstance.watchRoom();
        httpMock.expectOne(`${environment.apiUrl}asta/room/watch`).flush({ job_id: 'W1' });
        await vi.advanceTimersByTimeAsync(0);
        // Two rows, and they are lines **7 and 8** of a file that was already six rows
        // long. The count and the position are deliberately different numbers: when the
        // watch joins an evening in progress they always are, and a test in which they
        // agree cannot tell a viewer that resumes from the server's index from one that
        // resumes from its own tally.
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle({ index: 7 }), cycle({ index: 8 })], next_index: 8 }));
        await vi.advanceTimersByTimeAsync(0);

        await vi.advanceTimersByTimeAsync(2100);
        const second = httpMock.expectOne((r) => r.url.includes('asta/journal'));
        expect(second.request.params.get('since')).toBe('8');
        second.flush(tail({ next_index: 8 }));
        await vi.advanceTimersByTimeAsync(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it('takes the newest row of a tail, not the first', async () => {
      // The tail arrives oldest first — that is what makes it appendable — so the frame
      // is its *last* row. A catch-up poll after a backgrounded tab brings a hundred of
      // them, and showing the oldest would draw a lot that closed three minutes ago.
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(
          tail({
            rows: [
              cycle({ index: 1, name: 'Holm' }),
              cycle({ index: 2, name: 'Dimarco' }),
              cycle({ index: 3, name: 'Zaccagni' }),
            ],
            next_index: 3,
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.liveRow()?.name).toBe('Zaccagni');
    });

    it('leaves the frame alone when a poll brings no row', async () => {
      // A quiet poll is not a new frame of nothing. Overwriting on every response would
      // blank the walk-away two seconds after the last cycle — and a room between lots
      // is quiet for far longer than that.
      vi.useFakeTimers();
      try {
        const fixture = await ready([watching()]);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle()], next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);

        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail({ next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);

        expect(fixture.componentInstance.liveRow()?.name).toBe('Zaccagni');
      } finally {
        vi.useRealTimers();
      }
    });

    it('reattaches to a watch that was already running', async () => {
      // The run is a subprocess and outlives the tab. A page that re-enabled the button
      // instead would offer a second watch on the same room.
      const fixture = await ready([watching()]);

      expect(fixture.componentInstance.watchJobId()).toBe('W1');
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle({ name: 'Holm' })], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('Holm');
    });

    it('ignores a finished watch and a job of another kind', async () => {
      // `GET /jobs` lists everything the server has ever run this session. Reattaching to
      // a `done` row would tail a room that closed hours ago and call it live.
      const fixture = await ready([
        watching({ id: 'W0', status: 'done' }),
        watching({ id: 'H3', kind: 'harvest-collect' }),
      ]);

      expect(fixture.componentInstance.watchJobId()).toBeNull();
      httpMock.expectNone((r) => r.url.includes('asta/journal') && r.params.get('follow') === '1');
    });

    it('shows the server refusal for a link the room cannot be reached through', async () => {
      // The 400's `detail` is `parse_room_url`'s own sentence, which already names what to
      // paste instead. A message composed here would be a second opinion about the link.
      const fixture = await ready();
      fixture.componentInstance.setRoomUrl('https://example.com/nope');

      fixture.componentInstance.watchRoom();
      httpMock
        .expectOne(`${environment.apiUrl}asta/room/watch`)
        .flush(
          { detail: 'that is an invitation link, not a room link' },
          { status: 400, statusText: 'Bad Request' },
        );
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('not a room link');
      expect(fixture.componentInstance.watchJobId()).toBeNull();
    });

    it('draws the lot, the decision with its walk-away, and the rosa', async () => {
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle()], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      const text = fixture.nativeElement.textContent as string;
      // LOT: who is on the block, at what, and what the next raise costs.
      expect(text).toContain('Zaccagni');
      expect(text).toContain('40');
      // MODEL: the decision, and the walk-away with the provenance beside it — never
      // fused into it. A number nobody can argue with is one nobody can correct at 21:47.
      // Sentence case, not the CLI's capitals (`style_guide.md:295`); the colour says bid.
      const decision = fixture.nativeElement.querySelector('.live-decision') as HTMLElement;
      expect(decision.textContent?.trim()).toBe('Bid');
      expect(decision.classList).toContain('is-bid');
      expect(text).toContain('44');
      expect(text).toContain('re-solved with this lot forced in');
      // ROSA: the count, the credits, and the bargain pair that caps the unplanned lots.
      expect(text).toContain('312');
      expect(text).toContain('7');
      expect(text).toContain('12');
      expect(text).toContain('50');
    });

    it('renders a null walk-away as one, never as a zero', async () => {
      // 4,501 of the 5,192 recorded rows are null here — that is what defect B2 looks
      // like in the file, and a zero would read as "walk away at any price".
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle({ walk_away: null, provenance: null })], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      const pane = fixture.nativeElement.querySelector('.live-model') as HTMLElement;
      expect(pane.textContent).toContain('—');
      expect(pane.textContent).not.toContain('0 credits');
    });

    it('names the guard that refused when the decision was to hold', async () => {
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(
          tail({
            rows: [cycle({ decision: 'hold', reason: 'max_cap' })],
            next_index: 1,
          }),
        );
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.textContent).toContain('max_cap');
    });

    it('marks the planned target that is on the block', async () => {
      // The listone pane is the plan's, not the room's: the journal carries one lot per
      // row and the listone lives only in the child's `RoomTracker`. Saying which target
      // is up is the whole join between the two.
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      httpMock.expectOne(`${environment.apiUrl}jobs`).flush({ jobs: [watching()] });
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock
        .expectOne((r) => r.url.includes('asta/plan'))
        .flush(
          plan({
            players: [
              {
                player_id: '1',
                nome: 'Zaccagni',
                price: 41,
                walk_away: 44,
                walk_away_provenance: 're-solved',
              },
            ],
          }),
        );
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle({ name: 'Zaccagni' })], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.onTheBlock()).toBe('zaccagni');
      expect(fixture.nativeElement.querySelector('.is-on-the-block')).not.toBeNull();
    });

    it('renders cycle_ms as a first-class number and says when it is slow', async () => {
      // A slow loop that says it is slow is survivable; a silent one is not. The room's
      // own poll is 2 s (`interface/asta.py:518`), so a cycle slower than that is one
      // whose work now sets the cadence — the 72 s solve stall is what that looks like.
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle({ cycle_ms: 72000 })], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.cycleSlow()).toBe(true);
      const pane = fixture.nativeElement.querySelector('.live-cycle') as HTMLElement;
      expect(pane.textContent).toContain('72,000');
    });

    it('says a cycle was never timed rather than showing it as instant', async () => {
      // `cycle_ms` postdates the 2026-09-01 evening and is null across every recorded row.
      // Rendering that as 0 ms would report the stall it exists to expose as its opposite.
      const fixture = await ready([watching()]);
      httpMock
        .expectOne((r) => r.url.includes('asta/journal'))
        .flush(tail({ rows: [cycle({ cycle_ms: null })], next_index: 1 }));
      fixture.detectChanges();
      await fixture.whenStable();

      const pane = fixture.nativeElement.querySelector('.live-cycle') as HTMLElement;
      expect(pane.textContent).toContain('Not timed');
      expect(fixture.componentInstance.cycleSlow()).toBe(false);
    });

    it('counts the polls that brought nothing, because a quiet loop looks like a calm one', async () => {
      // The other half of the same problem: `cycle_ms` says the last cycle was slow, and
      // this says there has not been a cycle. On screen a stopped loop and a quiet room
      // are the same picture, and only one of them still bids.
      vi.useFakeTimers();
      try {
        const fixture = await ready([watching()]);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle()], next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);
        expect(fixture.componentInstance.quietPolls()).toBe(0);

        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail({ next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);
        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail({ next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);

        expect(fixture.componentInstance.quietPolls()).toBe(2);

        // And a row clears it. Without this the counter only ever climbs, so a run that
        // never reset it would read as a room that has been silent since it opened — the
        // alarm this exists to raise, raised permanently, which is the same as not at all.
        await vi.advanceTimersByTimeAsync(2100);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle({ index: 2 })], next_index: 2 }));
        await vi.advanceTimersByTimeAsync(0);

        expect(fixture.componentInstance.quietPolls()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });

    it('stops the watch and stops tailing once the job has actually ended', async () => {
      // **Re-cut at 3.9c.** This asserted that the id was cleared on the *stop response*,
      // which claimed an ended run the moment the request returned. `ProcessJob.stop`'s
      // stage one asks and returns; what says the child went is the job's own status. For
      // a watch the two are almost the same instant — it has nothing to disarm and leaves
      // on the first request — and for an armed bid they are not the same thing at all,
      // which is why the page reports what it asked and detaches on what happened.
      vi.useFakeTimers();
      try {
        const fixture = await ready([watching()]);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle()], next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);

        fixture.componentInstance.stopRun();
        httpMock.expectOne(`${environment.apiUrl}jobs/W1/stop`).flush({ ok: true });
        await vi.advanceTimersByTimeAsync(0);
        expect(fixture.componentInstance.watchJobId()).toBe('W1');

        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail({ next_index: 1 }));
        httpMock
          .expectOne(`${environment.apiUrl}jobs/W1`)
          .flush({ id: 'W1', status: 'done', lines: [], ok: true, error: null });
        await vi.advanceTimersByTimeAsync(0);

        expect(fixture.componentInstance.watchJobId()).toBeNull();
        // The last frame stays on screen. Blanking it would take the walk-away away at
        // the exact moment the operator has to bid by hand instead — `error_overlay`'s
        // reasoning, and the same trade.
        expect(fixture.componentInstance.liveRow()?.name).toBe('Zaccagni');

        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectNone((r) => r.url.includes('asta/journal'));
        httpMock.expectNone(`${environment.apiUrl}jobs/W1`);
      } finally {
        vi.useRealTimers();
      }
    });

    it('keeps the last frame under a banner when a poll fails', async () => {
      // Stale is still the right thing to keep drawing, and the banner is the only thing
      // that says it is stale: a failed poll leaves the previous frame indistinguishable,
      // on screen, from a quiet room where nothing has happened.
      vi.useFakeTimers();
      try {
        const fixture = await ready([watching()]);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle()], next_index: 1 }));
        await vi.advanceTimersByTimeAsync(0);

        await vi.advanceTimersByTimeAsync(2100);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .error(new ProgressEvent('error'), { status: 0, statusText: 'Unknown Error' });
        await vi.advanceTimersByTimeAsync(0);
        fixture.detectChanges();

        expect(fixture.componentInstance.liveRow()?.name).toBe('Zaccagni');
        expect(fixture.componentInstance.tailError()).not.toBeNull();

        // And it keeps polling: one failed read is not the end of the evening.
        await vi.advanceTimersByTimeAsync(2100);
        httpMock
          .expectOne((r) => r.url.includes('asta/journal'))
          .flush(tail({ rows: [cycle({ name: 'Holm' })], next_index: 2 }));
        await vi.advanceTimersByTimeAsync(0);

        expect(fixture.componentInstance.liveRow()?.name).toBe('Holm');
        expect(fixture.componentInstance.tailError()).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // -- 3.9c: the room view arms, and disarms ---------------------------------------------
  describe('live room — arming', () => {
    async function ready(jobs: JobSummary[] = []) {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      httpMock.expectOne(`${environment.apiUrl}jobs`).flush({ jobs });
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    function tail(over: Partial<JournalPage> = {}): JournalPage {
      return {
        ok: true,
        path: '/room_journal.jsonl',
        exists: true,
        total: 0,
        skipped: 0,
        offset: 0,
        limit: 100,
        next_index: 0,
        rows: [],
        error: null,
        ...over,
      };
    }

    it('has the run tag in the DOM, empty, before a run is started', async () => {
      // A live region inserted together with its first message is announced unreliably by
      // NVDA and JAWS — the region has to be there first and then fill. `news.spec.ts` has
      // the same test for the same reason; this one was gated by `@if (watchJobId())`, so
      // "Bidding · <id>" was the region's own birth and was lost every time.
      const fixture = await ready();

      const tag = (fixture.nativeElement as HTMLElement).querySelector('.live .tag');
      expect(tag).not.toBeNull();
      expect(tag?.getAttribute('aria-live')).toBe('polite');
      // Genuinely `:empty` — a stray whitespace node would leave an empty pill on the card.
      expect(tag?.matches(':empty')).toBe(true);
    });

    it('has the disarm status region in the DOM, empty, before the first stop', async () => {
      // The disarm note is the one message on this page an operator most needs announced:
      // it is what tells them the first Ctrl-C landed and the run is still drawing.
      const fixture = await bidding();
      const host = fixture.nativeElement as HTMLElement;

      const region = host.querySelector('.live [role="status"]');
      expect(region).not.toBeNull();
      expect(region?.matches(':empty')).toBe(true);
      expect(host.querySelector('[data-testid="disarm-note"]')).toBeNull();
    });

    it('names the disarm note only once it has something to announce', async () => {
      // The other half of the region test. The testid rides on the *text*, not on the host
      // that is now always present, so "disarm-note in the DOM <=> disarmed" stays a true
      // statement — leave it on the host and it reads as permanently disarmed.
      const fixture = await bidding();
      fixture.componentInstance.stopRun();
      httpMock.expectOne((r) => r.url.includes('/stop')).flush({ ok: true });
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const host = fixture.nativeElement as HTMLElement;
      const note = host.querySelector('[data-testid="disarm-note"]');
      expect(note).not.toBeNull();
      expect(note?.textContent).toContain('Disarm requested');
      expect(host.querySelector('.live [role="status"]')?.matches(':empty')).toBe(false);
    });

    /** Start a bid and flush its first tail. Returns the fixture. */
    async function bidding(body: Partial<BidStarted> = {}, arm = true) {
      const fixture = await ready();
      fixture.componentInstance.setRoomUrl('abc');
      fixture.componentInstance.setArmRequested(arm);
      fixture.componentInstance.bidRoom();
      httpMock.expectOne(`${environment.apiUrl}asta/room/bid`).flush({
        outcome: 'started',
        reason: '',
        job_id: 'B1',
        armed: arm,
        closed: arm ? [] : ['arm'],
        roster_size: 25,
        roster_provenance: 'read from the room',
        ...body,
      });
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    it('will not arm unless the page is told to, on the request that could act', async () => {
      // `application/arming`'s rule, rendered: a page can be reloaded, restored by the
      // session manager, or left open overnight, and none of those may carry an arming
      // decision forward. So the control starts off and the body always states it.
      const fixture = await ready();
      expect(fixture.componentInstance.armRequested()).toBe(false);

      fixture.componentInstance.setRoomUrl('abc');
      fixture.componentInstance.bidRoom();

      const started = httpMock.expectOne(`${environment.apiUrl}asta/room/bid`);
      expect(started.request.body).toEqual({ url: 'abc', arm: false });
      started.flush({
        outcome: 'started',
        reason: 'the request did not ask to arm',
        job_id: 'B1',
        armed: false,
        closed: ['arm'],
      });
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
      fixture.detectChanges();
      await fixture.whenStable();
    });

    it('names every shut lock, not the first', async () => {
      // The defect `application/arming` exists for: a ternary over two causes sends an
      // operator to fix one, retry, and be told about the other.
      const fixture = await bidding(
        {
          armed: false,
          closed: ['FANTABOT_AUTO_ACT', 'arm'],
          reason: 'FANTABOT_AUTO_ACT is false and the request did not ask to arm',
        },
        false,
      );

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('FANTABOT_AUTO_ACT');
      expect(text).toContain('did not ask to arm');
    });

    it('says on screen, unmissably, that a run is armed', async () => {
      // The one state where a mistaken click costs real credits. The CLI prints a bold red
      // line before the first poll for this reason; a page that looked the same armed and
      // disarmed would be worse, because there is no scrollback to check.
      const fixture = await bidding();

      const banner = fixture.nativeElement.querySelector('[data-testid="armed-banner"]');
      expect(banner).not.toBeNull();
      // Unmissable by role, colour and weight — not by capitals a screen reader may spell.
      expect(banner.getAttribute('role')).toBe('alert');
      expect(banner.querySelector('.is-error strong')?.textContent).toBe('Armed');
      expect(banner.textContent).toContain('this run places real bids');
    });

    it('shows no armed banner over a dry run', async () => {
      const fixture = await bidding({ armed: false, closed: ['arm'] }, false);

      expect(fixture.nativeElement.querySelector('[data-testid="armed-banner"]')).toBeNull();
    });

    it('a refused room starts nothing and shows the reason the server gave', async () => {
      const fixture = await ready();
      fixture.componentInstance.setRoomUrl('nope');
      fixture.componentInstance.bidRoom();
      httpMock.expectOne(`${environment.apiUrl}asta/room/bid`).flush({
        outcome: 'bad_link',
        reason: 'paste the app.fantalab.it/asta?asta= link',
        job_id: '',
        armed: false,
        closed: [],
      });
      fixture.detectChanges();
      await fixture.whenStable();

      // No tail: nothing was started. An outstanding request would fail `httpMock.verify`.
      expect(fixture.componentInstance.watchJobId()).toBeNull();
      expect(fixture.nativeElement.textContent).toContain('app.fantalab.it/asta?asta=');
    });

    it('says which band the run was started with', async () => {
      // The room check card shows what the *check* found; this is what the *child* was
      // told. They are the same number only because the route sends it — until it did, the
      // bidder planned and capped against `--lega`'s band, a different league entirely.
      const fixture = await bidding();

      const band = fixture.nativeElement.querySelector('[data-testid="run-band"]');
      expect(band).not.toBeNull();
      expect(band.textContent).toContain('25');
      expect(band.textContent).toContain('read from the room');
    });

    it('says when the band was assumed rather than declared', async () => {
      // The common case — `rules_for_room`'s own measurement is 153 of 247 rooms declaring
      // nothing. An assumed band is still better than another league's real one, and the
      // operator should be able to tell which they are looking at.
      const fixture = await bidding({
        roster_size: 30,
        roster_provenance: 'assumed — nothing was declared',
      });

      expect(fixture.nativeElement.querySelector('[data-testid="run-band"]').textContent).toContain(
        'assumed',
      );
    });

    it('shows no band over a watch, which plans nothing', async () => {
      const fixture = await ready();
      fixture.componentInstance.setRoomUrl('abc');
      fixture.componentInstance.watchRoom();
      httpMock.expectOne(`${environment.apiUrl}asta/room/watch`).flush({ job_id: 'W1' });
      fixture.detectChanges();
      await fixture.whenStable();
      httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.nativeElement.querySelector('[data-testid="run-band"]')).toBeNull();
    });

    it('claims no band for a run it reattached to', async () => {
      // `GET /jobs` does not carry it. Filling it in from the current room check would
      // claim the run used a number nobody has checked it did — and the room in the link
      // field may not even be the room that run is in.
      const fixture = await ready([
        {
          id: 'B9',
          kind: 'asta-bid',
          status: 'running',
          started_at: '2026-09-20T19:31:00Z',
          line_count: 3,
          ok: null,
          stoppable: true,
          armed: true,
        },
      ]);
      httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.watchJobId()).toBe('B9');
      expect(fixture.nativeElement.querySelector('[data-testid="run-band"]')).toBeNull();
    });

    it('the first stop disarms and the view keeps drawing', async () => {
      // 3.9c's own criterion: *"a stop that kills the view along with the bidding leaves
      // the operator blind mid-auction."* The run is still on the platform after the first
      // stop — disarmed, still deciding — and the walk-away on screen is what the operator
      // now has to bid by hand.
      vi.useFakeTimers();
      try {
        const fixture = await bidding();
        fixture.componentInstance.stopRun();
        httpMock.expectOne(`${environment.apiUrl}jobs/B1/stop`).flush({ ok: true });
        await vi.advanceTimersByTimeAsync(0);
        fixture.detectChanges();

        expect(fixture.componentInstance.watchJobId()).toBe('B1');
        expect(fixture.componentInstance.stopsAsked()).toBe(1);

        // The tail is still running: the next tick asks for rows, and the job is still
        // reported as running, so nothing detaches.
        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
        httpMock
          .expectOne(`${environment.apiUrl}jobs/B1`)
          .flush({ id: 'B1', status: 'running', lines: [], ok: null, error: null });
        await vi.advanceTimersByTimeAsync(0);
        fixture.detectChanges();

        expect(fixture.componentInstance.watchJobId()).toBe('B1');
        expect(fixture.nativeElement.textContent).toContain('Disarm requested');
      } finally {
        vi.useRealTimers();
      }
    });

    it('detaches only once the server says the job has ended', async () => {
      // The page reports what it *asked*; the job's own status is what says it happened.
      // A page that cleared the id on the stop response would claim an ended run over one
      // that is still deciding — and stop tailing the only channel that would say so.
      vi.useFakeTimers();
      try {
        const fixture = await bidding();
        fixture.componentInstance.stopRun();
        httpMock.expectOne(`${environment.apiUrl}jobs/B1/stop`).flush({ ok: true });
        await vi.advanceTimersByTimeAsync(0);

        fixture.componentInstance.stopRun();
        httpMock.expectOne(`${environment.apiUrl}jobs/B1/stop`).flush({ ok: true });
        await vi.advanceTimersByTimeAsync(0);
        expect(fixture.componentInstance.stopsAsked()).toBe(2);
        // Still attached: two requests is not two confirmations.
        expect(fixture.componentInstance.watchJobId()).toBe('B1');

        await vi.advanceTimersByTimeAsync(2100);
        httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
        httpMock
          .expectOne(`${environment.apiUrl}jobs/B1`)
          .flush({ id: 'B1', status: 'done', lines: [], ok: true, error: null });
        await vi.advanceTimersByTimeAsync(0);
        fixture.detectChanges();

        expect(fixture.componentInstance.watchJobId()).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });

    it('a stop the server refuses keeps the run attached and says why', async () => {
      // A 409 is the server saying the job has no way to be stopped — not that stopping
      // failed. Detaching on it would leave a live child with nothing watching it.
      const fixture = await bidding();
      fixture.componentInstance.stopRun();
      httpMock
        .expectOne(`${environment.apiUrl}jobs/B1/stop`)
        .flush({ detail: 'this job cannot be stopped' }, { status: 409, statusText: 'Conflict' });
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.watchJobId()).toBe('B1');
      expect(fixture.componentInstance.stopsAsked()).toBe(0);
      expect(fixture.nativeElement.textContent).toContain('cannot be stopped');
    });

    it('reattaches a bid that was already running, and knows it is not a watch', async () => {
      // `GET /jobs` is the source of truth. A page that only looked for `asta-watch` would
      // offer to start a second run on a room that already has one bidding in it.
      const fixture = await ready([
        {
          id: 'B9',
          kind: 'asta-bid',
          status: 'running',
          started_at: '2026-09-20T19:31:00Z',
          line_count: 3,
          ok: null,
          stoppable: true,
        },
      ]);
      httpMock.expectOne((r) => r.url.includes('asta/journal')).flush(tail());
      fixture.detectChanges();
      await fixture.whenStable();

      expect(fixture.componentInstance.watchJobId()).toBe('B9');
      expect(fixture.componentInstance.runKind()).toBe('bid');
    });
  });

  // -- 3.10: the rolling advisory over a live room's ledger -------------------------------
  describe('advisory', () => {
    async function ready() {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      return fixture;
    }

    function resolved(over: Partial<RoomCheck> = {}): RoomCheck {
      return {
        outcome: 'resolved',
        reason: '',
        fantaleague_id: 'abc',
        shard: 4,
        asta_type: 'mantra',
        asta_mode: 'chiamata',
        raise_mode: 'free',
        num_teams: 10,
        num_credits: 650,
        seat_team_id: 'TEAM-7',
        seat_team_name: 'Legamiallerotaie',
        seat_user_id: 'USER-9',
        roster_size: 25,
        roster_provenance: 'read from the room',
        ...over,
      };
    }

    /** Check a room, then ask for its advisory. Returns the captured request. */
    async function advise(
      fixture: ComponentFixture<AstaComponent>,
      body: Record<string, unknown>,
    ) {
      fixture.componentInstance.setRoomUrl('abc');
      fixture.componentInstance.checkRoom();
      httpMock.expectOne((r) => r.url.includes('asta/room')).flush(resolved());
      fixture.detectChanges();
      await fixture.whenStable();

      fixture.componentInstance.loadAdvisory();
      const asked = httpMock.expectOne((r) => r.url.includes('asta/advisory'));
      asked.flush(body);
      fixture.detectChanges();
      await fixture.whenStable();
      return asked;
    }

    it('asks with the shard, the seat and the room’s own shape', async () => {
      // Every one of these is a field `asta live` cannot read for itself, and each has a
      // default that is a different lega's game. They come from the room check the
      // operator has already run, not from anything this page decided.
      const fixture = await ready();
      const asked = await advise(fixture, {
        outcome: 'advised',
        reason: '',
        targets: [],
        opponents: [],
        sales: 0,
        dropped_sales: 0,
        total_cost: 0,
        objective: 0,
      });

      expect(asked.request.params.get('league')).toBe('abc');
      expect(asked.request.params.get('db')).toBe('4');
      expect(asked.request.params.get('team')).toBe('TEAM-7');
      // The format above all: it selects both the pool and the corpus, so a Classic room
      // advised as Mantra is headed by players it cannot call, priced off another game.
      expect(asked.request.params.get('listone')).toBe('mantra');
      expect(asked.request.params.get('teams')).toBe('10');
      expect(asked.request.params.get('credits')).toBe('650');
    });

    it('renders a chase and a freely-replaceable target differently', async () => {
      // `reservations` clamps a negative marginal to zero — he is freely replaceable — and
      // the bidder refuses at every price, because its smallest raise is `current + step`.
      // A row saying "chase, walk-away 0" reads as an instruction to do the one thing the
      // system will not do. He stays on the list: he is in the target roster.
      const fixture = await ready();
      await advise(fixture, {
        outcome: 'advised',
        reason: '',
        targets: [
          { player_id: '2', nome: 'Zaccagni', walk_away: 44, chase: true },
          { player_id: '1', nome: 'Svilar', walk_away: 0, chase: false },
        ],
        opponents: [],
        sales: 3,
        dropped_sales: 0,
        total_cost: 412,
        objective: 1897,
      });

      const rows = Array.from(
        fixture.nativeElement.querySelectorAll('[data-testid="advisory-target"]'),
      ) as HTMLElement[];
      expect(rows.length).toBe(2);
      expect(rows[0].textContent).toContain('Zaccagni');
      expect(rows[0].textContent).toContain('44');
      expect(rows[1].textContent).toContain('freely replaceable');
      expect(rows[1].textContent).not.toContain('Chase');
    });

    it('shows every rival and what it has left', async () => {
      const fixture = await ready();
      await advise(fixture, {
        outcome: 'advised',
        reason: '',
        targets: [],
        opponents: [{ team_id: 'THEM', players: 3, spent: 120, remaining: 530 }],
        sales: 3,
        dropped_sales: 0,
        total_cost: 0,
        objective: 0,
      });

      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('THEM');
      expect(text).toContain('530');
    });

    it('says when a sale could not be named rather than quietly losing it', async () => {
      // Each dropped sale is a purchase nobody subtracted, so a rival's budget and that
      // player's availability are both wrong until it is explained.
      const fixture = await ready();
      await advise(fixture, {
        outcome: 'advised',
        reason: '',
        targets: [],
        opponents: [],
        sales: 9,
        dropped_sales: 2,
        total_cost: 0,
        objective: 0,
      });

      expect(fixture.nativeElement.textContent).toContain('2 sale');
    });

    it('counts one sale as a sale, not as "sale(s)"', async () => {
      const fixture = await ready();
      await advise(fixture, {
        outcome: 'advised',
        reason: '',
        targets: [],
        opponents: [],
        sales: 1,
        dropped_sales: 1,
        total_cost: 0,
        objective: 0,
      });

      const text = (fixture.nativeElement.textContent as string).replace(/\s+/g, ' ');
      expect(text).toContain('1 sale folded');
      expect(text).toContain('1 sale dropped');
      expect(text).not.toContain('(s)');
    });

    it('names the refusal instead of drawing an empty advisory', async () => {
      // An outage rendered as "no targets" is a false statement, not a missing one — and it
      // is the state in which an operator decides they have nothing to chase.
      const fixture = await ready();
      await advise(fixture, {
        outcome: 'unreachable',
        reason: 'OSError: connection reset',
        targets: [],
        opponents: [],
        sales: 0,
        dropped_sales: 0,
        total_cost: 0,
        objective: 0,
      });

      expect(fixture.nativeElement.textContent).toContain('connection reset');
      expect(fixture.nativeElement.querySelectorAll('[data-testid="advisory-target"]').length).toBe(
        0,
      );
    });

    it('cannot be asked for before a room has resolved', async () => {
      // The shard and the seat come from the check. Without them the request would carry
      // the defaults, and an advisory priced against another lega's game is worse than none.
      const fixture = await ready();

      fixture.componentInstance.loadAdvisory();

      httpMock.expectNone((r) => r.url.includes('asta/advisory'));
    });
  });

  /**
   * The M3 pass over this page: one primary action, states that say what failed and offer
   * the way back, controls grouped with what they affect, and copy that stays true.
   */
  describe('states, hierarchy and copy', () => {
    async function withLeagues(ids: number[]) {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).flush(ids.map((id) => overview(id)));
      fixture.detectChanges();
      await fixture.whenStable();
      return fixture;
    }

    function buttonNamed(root: HTMLElement, name: string): HTMLButtonElement {
      const match = Array.from(root.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === name,
      );
      if (!match) throw new Error(`no button named ${name}`);
      return match;
    }

    it('says a failed plan read failed, and retries it with focus on the heading', async () => {
      // It used to render nothing at all: `plan()` null, `planLoading()` false, and the pane
      // under a selected lega simply empty.
      const fixture = await withLeagues([4103937]);
      httpMock
        .expectOne(`${environment.apiUrl}asta/plan?league_id=4103937`)
        .error(new ProgressEvent('error'));
      fixture.detectChanges();
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      expect(root.querySelector('.overview-main [role="alert"]')?.textContent).toContain(
        "Couldn't load the plan",
      );

      const retry = buttonNamed(root, 'Try again');
      retry.focus();
      retry.click();
      fixture.detectChanges();
      // The button removed itself by starting the load; focus did not fall to <body>.
      expect(document.activeElement).toBe(root.querySelector('h1'));

      httpMock.expectOne(`${environment.apiUrl}asta/plan?league_id=4103937`).flush(plan());
      fixture.detectChanges();
      await fixture.whenStable();
      expect(root.textContent).toContain('Svilar');
      expect(root.querySelector('.overview-main [role="alert"]')).toBeNull();
    });

    it('keeps waiting on the lega it is on when the one it left fails late', async () => {
      const fixture = await withLeagues([1, 2]);
      const left = httpMock.expectOne(`${environment.apiUrl}asta/plan?league_id=1`);
      fixture.componentInstance.select(2);
      const current = httpMock.expectOne(`${environment.apiUrl}asta/plan?league_id=2`);

      left.error(new ProgressEvent('error'));
      fixture.detectChanges();
      expect(fixture.componentInstance.planLoading()).toBe(true);
      expect(fixture.componentInstance.planError()).toBe(false);

      current.flush(plan());
      fixture.detectChanges();
      expect(fixture.componentInstance.plan()?.found).toBe(true);
    });

    it('offers a retry when the leghe cannot be read, and a way to sync when there are none', async () => {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
      flushExclusions();
      flushJobs();
      httpMock.expectOne(`${environment.apiUrl}lega`).error(new ProgressEvent('error'));
      fixture.detectChanges();
      await fixture.whenStable();

      const root = fixture.nativeElement as HTMLElement;
      expect(root.querySelector('[role="alert"]')?.textContent).toContain(
        "Couldn't load your leghe",
      );
      buttonNamed(root, 'Try again').click();
      fixture.detectChanges();
      expect(document.activeElement).toBe(root.querySelector('h1'));

      httpMock.expectOne(`${environment.apiUrl}lega`).flush([]);
      fixture.detectChanges();
      await fixture.whenStable();
      expect(root.textContent).toContain('No leagues yet');
      expect(root.querySelector('a[href="/synchronize"]')?.textContent?.trim()).toBe(
        'Go to Synchronize',
      );
    });

    it('explains each walk-away kind once and keeps only the kind on the row', async () => {
      stubSizeClass('medium');
      const resolvedWhy = 're-solved: the most this rosa would pay before it is no better off';
      const holdWhy = 'hold: a substitute exists — the rosa does not improve by buying him';
      const fixture = await readyWithPlan(
        plan({
          players: [
            {
              player_id: '1',
              nome: 'A',
              price: 30,
              walk_away: 34,
              walk_away_provenance: resolvedWhy,
            },
            { player_id: '2', nome: 'B', price: 20, walk_away: 0, walk_away_provenance: holdWhy },
            {
              player_id: '3',
              nome: 'C',
              price: 10,
              walk_away: 12,
              walk_away_provenance: resolvedWhy,
            },
          ],
        }),
      );
      const root = fixture.nativeElement as HTMLElement;

      expect(
        Array.from(root.querySelectorAll('.legend dt')).map((d) => d.textContent?.trim()),
      ).toEqual(['Re-solved', 'Hold']);
      expect(
        Array.from(root.querySelectorAll('table.targets-table tbody .prov')).map((p) =>
          p.textContent?.trim(),
        ),
      ).toEqual(['Re-solved', 'Hold', 'Re-solved']);
      // Each sentence is on the page exactly once.
      const text = root.textContent ?? '';
      expect(text.split('a substitute exists').length - 1).toBe(1);
      expect(text.split('no better off').length - 1).toBe(1);
    });

    it('shows a provenance it does not recognise whole, with nothing to put in the legend', async () => {
      stubSizeClass('compact');
      const fixture = await readyWithPlan(
        plan({
          players: [
            { player_id: '1', nome: 'A', price: 30, walk_away: 34, walk_away_provenance: 'manual' },
          ],
        }),
      );
      const root = fixture.nativeElement as HTMLElement;

      expect(root.querySelector('.legend')).toBeNull();
      expect(root.querySelector('.target-card .prov')?.textContent?.trim()).toBe('Manual');
    });

    it('groups the arm with the bid it arms, apart from Watch room', async () => {
      const fixture = await withLeagues([]);
      const group = (fixture.nativeElement as HTMLElement).querySelector(
        '.bid-group[role="group"]',
      ) as HTMLElement;

      expect(group).not.toBeNull();
      expect(group.querySelector('[data-testid="arm-checkbox"]')).not.toBeNull();
      expect(group.querySelector('[data-testid="bid-button"]')).not.toBeNull();
      expect(group.textContent).not.toContain('Watch room');
    });

    it('has one filled button once a plan is on screen', async () => {
      // One primary action per view (`usability/overview.md:105`). Check room is it: it
      // gates the watch, the bid and the advisory.
      stubSizeClass('expanded');
      const fixture = await readyWithPlan(plan());
      const filled = Array.from(
        (fixture.nativeElement as HTMLElement).querySelectorAll('.mat-mdc-unelevated-button'),
      ).map((b) => b.textContent?.trim());

      expect(filled).toEqual(['Check room']);
    });

    it('gives examples as hints, never as placeholders that read as typed values', async () => {
      const fixture = await withLeagues([]);
      const root = fixture.nativeElement as HTMLElement;

      for (const id of ['room-url', 'exclude-player', 'exclude-reason', 'exclude-source']) {
        const field = root.querySelector(`#${id}`)?.closest('mat-form-field');
        expect(field?.classList).not.toContain('mat-mdc-form-field-label-always-float');
      }
      for (const id of ['exclude-player', 'exclude-reason', 'exclude-source']) {
        expect(root.querySelector(`#${id}`)?.getAttribute('placeholder')).toBeNull();
      }
      expect(
        Array.from(root.querySelectorAll('.exclude-form mat-hint')).map((h) =>
          h.textContent?.trim(),
        ),
      ).toEqual([
        'For example, 4344',
        'For example, left Serie A 2026-08-30',
        'Where you read it, for example goal.com',
      ]);
    });

    it('labels a decision in sentence case, not the CLI capitals', () => {
      // No `detectChanges`, so `ngOnInit` never runs and no request is made.
      const component = TestBed.createComponent(AstaComponent).componentInstance;
      expect(component.decisionLabel('bid')).toBe('Bid');
      expect(component.decisionLabel(null)).toBe('Waiting');
    });
  });
});
