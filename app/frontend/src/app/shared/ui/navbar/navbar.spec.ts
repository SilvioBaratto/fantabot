import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NavbarComponent } from './navbar';

@Component({
  imports: [NavbarComponent],
  template: `
    <app-navbar>
      <span navbarBrand>My App</span>
      <button navbarActions aria-label="Settings">S</button>
    </app-navbar>
  `,
})
class HostComponent {}

function header(f: ComponentFixture<unknown>): HTMLElement {
  return f.nativeElement.querySelector('header')!;
}

// ── pt-safe ─────────────────────────────────────────────────────────────────

describe('NavbarComponent — pt-safe', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HostComponent] }).compileComponents();
  });

  it('when the navbar renders, the header element carries the pt-safe class', () => {
    const f = TestBed.createComponent(HostComponent);
    f.detectChanges();
    expect(header(f).classList).toContain('pt-safe');
  });
});

// ── brand slot ───────────────────────────────────────────────────────────────

describe('NavbarComponent — brand projection', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HostComponent] }).compileComponents();
  });

  it('when brand content is projected, it appears inside the header', () => {
    const f = TestBed.createComponent(HostComponent);
    f.detectChanges();
    expect(header(f).textContent).toContain('My App');
  });
});

// ── actions slot ─────────────────────────────────────────────────────────────

describe('NavbarComponent — actions projection', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [HostComponent] }).compileComponents();
  });

  it('when actions content is projected, it appears inside the header', () => {
    const f = TestBed.createComponent(HostComponent);
    f.detectChanges();
    const btn = header(f).querySelector('button[aria-label="Settings"]');
    expect(btn).not.toBeNull();
  });
});

// ── dark-mode tokens ─────────────────────────────────────────────────────────

describe('NavbarComponent — dark-mode tokens', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [NavbarComponent] }).compileComponents();
  });

  it('when rendered, no hardcoded hex colours appear in inline element styles', () => {
    const f = TestBed.createComponent(NavbarComponent);
    f.detectChanges();
    const hexPattern = /#[0-9a-fA-F]{3,8}\b/;
    const root = f.nativeElement as HTMLElement;
    const els: HTMLElement[] = [root, ...Array.from(root.querySelectorAll('*') as NodeListOf<HTMLElement>)];
    for (const el of els) {
      expect(el.getAttribute('style') ?? '').not.toMatch(hexPattern);
    }
  });

  it('when rendered, the header carries bg-surface-raised token class', () => {
    const f = TestBed.createComponent(NavbarComponent);
    f.detectChanges();
    expect(header(f).classList).toContain('bg-surface-raised');
  });

  it('when rendered, the header carries border-border token class', () => {
    const f = TestBed.createComponent(NavbarComponent);
    f.detectChanges();
    expect(header(f).classList).toContain('border-border');
  });
});
