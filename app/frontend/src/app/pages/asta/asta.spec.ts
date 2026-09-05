import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
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

  /**
   * The room check. Its own section, and its own outcomes: `todo/TODO.md` §3.4 records
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

      const labels = Array.from(
        fixture.nativeElement.querySelectorAll('button'),
      ).map((b) => ((b as HTMLButtonElement).textContent ?? '').toLowerCase());
      expect(labels.some((l) => /bid|arm|raise|offer/.test(l))).toBe(false);
    });
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
