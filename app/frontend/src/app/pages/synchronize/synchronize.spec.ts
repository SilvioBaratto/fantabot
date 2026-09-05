import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { SynchronizeComponent } from './synchronize';

describe('SynchronizeComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SynchronizeComponent],
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

  it('starts a lega sync job for the entered league id', () => {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    fixture.componentInstance.setLeagueId('4103937');
    fixture.componentInstance.runLegaSync();

    httpMock
      .expectOne((r) => r.url.includes('actions/lega-sync') && r.url.includes('4103937'))
      .flush({ job_id: 'J1' });
    fixture.detectChanges();

    expect(fixture.componentInstance.running()).toBe(true);
    // poll() starts interval(1500); no timer advance -> no /jobs request. Destroy to cancel.
    fixture.destroy();
  });

  it('does not start when no league id is entered', () => {
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    fixture.componentInstance.runLegaSync();

    httpMock.expectNone((r) => r.url.includes('actions/lega-sync'));
    expect(fixture.componentInstance.running()).toBe(false);
  });

  it('offers only the lega sync', () => {
    // News fetch moved off this page; the endpoint and ActionsService.runNewsFetch
    // both remain, for wherever it lands next.
    const fixture = TestBed.createComponent(SynchronizeComponent);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Lega sync');
    expect(text).not.toContain('News fetch');
    httpMock.expectNone((r) => r.url.includes('actions/news-fetch'));
  });
});
