import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from './../../icons';
import { SidebarComponent } from './sidebar';

describe('SidebarComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SidebarComponent],
      providers: [
        provideRouter([]),
        ICON_PROVIDER,
        {
          provide: LucideIconConfig,
          useFactory: () => {
            const cfg = new LucideIconConfig();
            cfg.size = 16;
            cfg.strokeWidth = 1.5;
            return cfg;
          },
        },
      ],
    }).compileComponents();
  });

  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(SidebarComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture.nativeElement;
  }

  it('when the sidebar renders, a lucide-icon element is present', async () => {
    const el = await render();
    expect(el.querySelector('lucide-icon')).toBeTruthy();
  });

  it('when the nav renders, the Home, Dashboard and Settings labels are shown', async () => {
    const el = await render();
    const navText = el.querySelector('nav')!.textContent ?? '';
    expect(navText).toContain('Home');
    expect(navText).toContain('Dashboard');
    expect(navText).toContain('Settings');
  });
});
