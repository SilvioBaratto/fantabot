import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from './../../icons';
import { BottomTabBarComponent } from './bottom-tab-bar';

describe('BottomTabBarComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BottomTabBarComponent],
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

  async function renderTabs(): Promise<HTMLAnchorElement[]> {
    const fixture = TestBed.createComponent(BottomTabBarComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return Array.from(fixture.nativeElement.querySelectorAll('nav a'));
  }

  it('when the bar renders, three tabs are returned', async () => {
    const tabs = await renderTabs();
    expect(tabs.length).toBe(3);
  });

  it('when each tab renders, an aria-label is present', async () => {
    const tabs = await renderTabs();
    expect(tabs.every((t) => !!t.getAttribute('aria-label'))).toBe(true);
  });

  it('when each tab renders, a lucide-icon is present', async () => {
    const tabs = await renderTabs();
    expect(tabs.every((t) => !!t.querySelector('lucide-icon'))).toBe(true);
  });

  it('when the tabs render, the Home, Dashboard and Settings labels are shown', async () => {
    const tabs = await renderTabs();
    const text = tabs.map((t) => t.textContent).join(' ');
    expect(text).toContain('Home');
    expect(text).toContain('Dashboard');
    expect(text).toContain('Settings');
  });

  it('when a tab is active, aria-current page is exposed on the anchor', async () => {
    // Use a real /home route so RouterLinkActive activates and sets aria-current.
    const { RouterTestingModule } = await import('@angular/router/testing');
    await TestBed.configureTestingModule({
      imports: [BottomTabBarComponent],
      providers: [
        provideRouter([{ path: 'home', component: BottomTabBarComponent }]),
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

    const router = TestBed.inject(Router);
    await router.navigate(['/home']);
    const fixture = TestBed.createComponent(BottomTabBarComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    const activeAnchor = fixture.nativeElement.querySelector('nav a[aria-current="page"]');
    expect(activeAnchor).toBeTruthy();
  });
});
