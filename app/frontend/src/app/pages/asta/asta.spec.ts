import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { AstaPlan } from '../../core/models/asta-plan';
import { JournalPage, JournalRow } from '../../core/models/journal';
import { RoomCheck } from '../../core/models/room';
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

    httpMock.expectOne(`${environment.apiUrl}lega`).flush([overview(4103937)]);
    fixture.detectChanges();
    await fixture.whenStable();

    httpMock.expectOne((r) => r.url.includes('asta/plan')).flush(plan());
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Svilar');
    expect(text).toContain('objective');
  });

  it('says what the plan was built on', async () => {
    // The page showed a number and none of the inputs behind it, and those inputs
    // differed from the command's in ten places (SPEC.md §11.1).
    const fixture = await readyWithPlan(plan({ lam: 0.3, owned: ['9'], callable_pool: 529 }));

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('risk (lam)');
    expect(text).toContain('0.3');
    expect(text).toContain('callable pool');
    expect(text).toContain('529');
  });

  it('renders an unnarrowed pool as unnarrowed, not as zero', async () => {
    // `null` means the listone was unreachable and the plan ran over the whole pool. A
    // pool narrowed to nothing is a different fact and would be a plan over nobody.
    const fixture = await readyWithPlan(plan({ callable_pool: null }));

    expect(fixture.nativeElement.textContent).toContain('not narrowed');
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
   * The room check. Its own section, and its own outcomes: T31 (`tasks/BACKLOG.md`) records
   * what one label over four failures costs, so the screen must render five different
   * answers differently. It is also the only surface that tells the operator whether the
   * stored FantaLab credential still works.
   */
  describe('room check', () => {
    async function ready() {
      const fixture = TestBed.createComponent(AstaComponent);
      fixture.detectChanges();
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

    it('offers nothing that could bid', async () => {
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
        roster_size: 25,
        roster_provenance: 'read from the room',
      });

      const labels = Array.from(fixture.nativeElement.querySelectorAll('button')).map((b) =>
        ((b as HTMLButtonElement).textContent ?? '').toLowerCase(),
      );
      expect(labels.some((l) => /bid|arm|raise|offer/.test(l))).toBe(false);
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
      // The label, not a docstring: this is what the CLI decided, not what the app did.
      expect(text.toLowerCase()).toContain('cli');
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
  });

  it('shows a no-plan state when the pool is empty', async () => {
    const fixture = TestBed.createComponent(AstaComponent);
    fixture.detectChanges();

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
    expect(text).toContain('re-solved');
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
});
