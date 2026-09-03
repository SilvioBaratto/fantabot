import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { environment } from '../../../environments/environment';
import { ICON_PROVIDER } from '../../icons';
import { AccountsComponent } from './accounts';

describe('AccountsComponent', () => {
  let httpMock: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AccountsComponent],
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

  function flush(body: object) {
    httpMock.expectOne(`${environment.apiUrl}auth/status`).flush(body);
  }

  it('renders a connected league with its state', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({
      has_key: true,
      fantalab: [{ user_id: 'user9', captured_at: '2026-09-01T00:00:00Z', last_used_at: null }],
      leagues: [
        {
          league_id: 4103937,
          league_name: 'Legamiallerotaie2',
          state: 'ok (364d)',
          expires_at: '2027-09-03T00:00:00Z',
          last_verified_at: null,
          user_id: 1,
          team_id: 2,
        },
      ],
    });
    fixture.detectChanges();
    await fixture.whenStable();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Legamiallerotaie2');
    expect(text).toContain('ok (364d)');
    expect(text).toContain('user9');
  });

  it('shows an empty state when no leagues are connected', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('No account connected yet');
  });

  it('warns when no encryption key is set', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: false, fantalab: [], leagues: [] });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(fixture.nativeElement.textContent).toContain('FANTABOT_ENCRYPTION_KEY');
  });

  it('starts a login job and confirms it', async () => {
    const fixture = TestBed.createComponent(AccountsComponent);
    fixture.detectChanges();
    flush({ has_key: true, fantalab: [], leagues: [] });
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.onConnect();
    httpMock
      .expectOne((r) => r.url.includes('auth/login') && !r.url.includes('confirm'))
      .flush({ job_id: 'J1' });
    fixture.detectChanges();
    expect(fixture.componentInstance.connecting()).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('Sign in in the browser window');

    fixture.componentInstance.onContinue();
    httpMock.expectOne((r) => r.url.includes('/confirm')).flush({ ok: true });

    // pollUntilDone starts interval(1500); with no timer advance it never emits, so no
    // /jobs request is pending. Destroy the fixture to cancel it before verify.
    fixture.destroy();
  });
});
