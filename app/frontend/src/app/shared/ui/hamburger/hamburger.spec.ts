import { ComponentFixture, TestBed } from '@angular/core/testing';
import { LucideIconConfig } from 'lucide-angular';

import { ICON_PROVIDER } from '../../../icons';
import { HamburgerComponent } from './hamburger';

function btn(f: ComponentFixture<HamburgerComponent>): HTMLButtonElement {
  return f.nativeElement.querySelector('button')!;
}

async function setup(): Promise<ComponentFixture<HamburgerComponent>> {
  await TestBed.configureTestingModule({
    imports: [HamburgerComponent],
    providers: [
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
  const f = TestBed.createComponent(HamburgerComponent);
  f.detectChanges();
  return f;
}

// ── aria-expanded ─────────────────────────────────────────────────────────────

describe('HamburgerComponent — aria-expanded', () => {
  it('when open is false, aria-expanded is "false"', async () => {
    const f = await setup();
    f.componentRef.setInput('open', false);
    f.detectChanges();
    expect(btn(f).getAttribute('aria-expanded')).toBe('false');
  });

  it('when open is true, aria-expanded is "true"', async () => {
    const f = await setup();
    f.componentRef.setInput('open', true);
    f.detectChanges();
    expect(btn(f).getAttribute('aria-expanded')).toBe('true');
  });
});

// ── aria-controls ─────────────────────────────────────────────────────────────

describe('HamburgerComponent — aria-controls', () => {
  it('when controls is set, aria-controls matches the given id', async () => {
    const f = await setup();
    f.componentRef.setInput('controls', 'main-drawer');
    f.detectChanges();
    expect(btn(f).getAttribute('aria-controls')).toBe('main-drawer');
  });
});

// ── 44px touch target ─────────────────────────────────────────────────────────

describe('HamburgerComponent — 44px touch target', () => {
  it('when rendered, the button has min-h-11 for the 44px minimum height', async () => {
    const f = await setup();
    expect(btn(f).classList).toContain('min-h-11');
  });

  it('when rendered, the button has min-w-11 for the 44px minimum width', async () => {
    const f = await setup();
    expect(btn(f).classList).toContain('min-w-11');
  });
});

// ── visible focus ─────────────────────────────────────────────────────────────

describe('HamburgerComponent — visible focus ring', () => {
  it('when rendered, the button has a focus-visible ring class', async () => {
    const f = await setup();
    const cls = btn(f).className;
    expect(cls).toContain('focus-visible:ring-');
  });
});

// ── toggle output ─────────────────────────────────────────────────────────────

describe('HamburgerComponent — toggle output', () => {
  it('when the button is clicked, toggle event is emitted', async () => {
    const f = await setup();
    let count = 0;
    f.componentInstance.toggle.subscribe(() => { count++; });
    btn(f).click();
    expect(count).toBe(1);
  });
});

// ── aria-label reflects state ─────────────────────────────────────────────────

describe('HamburgerComponent — aria-label', () => {
  it('when open is false, aria-label is "Open menu"', async () => {
    const f = await setup();
    f.componentRef.setInput('open', false);
    f.detectChanges();
    expect(btn(f).getAttribute('aria-label')).toBe('Open menu');
  });

  it('when open is true, aria-label is "Close menu"', async () => {
    const f = await setup();
    f.componentRef.setInput('open', true);
    f.detectChanges();
    expect(btn(f).getAttribute('aria-label')).toBe('Close menu');
  });
});

// ── icon switches with open state ─────────────────────────────────────────────

describe('HamburgerComponent — icon', () => {
  it('when open is false, a lucide-icon is rendered', async () => {
    const f = await setup();
    f.componentRef.setInput('open', false);
    f.detectChanges();
    expect(f.nativeElement.querySelector('lucide-icon')).toBeTruthy();
  });

  it('when open is true, a lucide-icon is rendered', async () => {
    const f = await setup();
    f.componentRef.setInput('open', true);
    f.detectChanges();
    expect(f.nativeElement.querySelector('lucide-icon')).toBeTruthy();
  });
});
