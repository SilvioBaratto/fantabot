import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { NewsComponent } from './news';

describe('NewsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [NewsComponent],
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

  function flush(feed: object[], drifted: object[]): void {
    httpMock
      .expectOne((r) => r.url.startsWith(`${environment.apiUrl}news`) && !r.url.includes('drifted'))
      .flush(feed);
    httpMock.expectOne((r) => r.url.includes('news/drifted')).flush(drifted);
  }

  it('renders the feed and the drift list', async () => {
    const fixture = TestBed.createComponent(NewsComponent);
    fixture.detectChanges();
    flush(
      [
        {
          player_id: '2',
          nome: 'Zaccagni',
          sentiment: 0.9,
          disponibilita: 1,
          titolarita: 0.8,
          forma: 0.5,
          confidenza: 0.9,
          ruoli_mantra: 'W;T',
        },
      ],
      [{ player_id: '2', nome: 'Zaccagni', ruoli_mantra: 'C', ruolo_campo: 'W', deriva_ruolo: 2 }],
    );
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Zaccagni');
    expect(text).toContain('Role drift');
    expect(text).toContain('Sentiment');
  });

  it('shows an empty state when there is no news', async () => {
    const fixture = TestBed.createComponent(NewsComponent);
    fixture.detectChanges();
    flush([], []);
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No news yet');
  });
});
