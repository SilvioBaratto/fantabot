import { OverlayContainer } from '@angular/cdk/overlay';
import { ApplicationRef } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { ICON_PROVIDER } from '../../../icons';
import { ToastComponent } from './toast';
import { ToastService, ToastVariant } from './toast.service';

describe('ToastService', () => {
  let service: ToastService;

  beforeEach(() => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({});
    service = TestBed.inject(ToastService);
  });

  afterEach(() => {
    service.toasts().forEach((t) => service.dismiss(t.id));
    vi.useRealTimers();
  });

  // --- signal state ---

  it('when created, the toast list is empty', () => {
    expect(service.toasts()).toEqual([]);
  });

  // --- show ---

  it('when show is called, the returned id is a number', () => {
    const id = service.show('info', 'hello');
    expect(typeof id).toBe('number');
    service.dismiss(id);
  });

  it('when show is called, the toast appears in the signal list', () => {
    const id = service.show('info', 'hello');
    expect(service.toasts().length).toBe(1);
    expect(service.toasts()[0].message).toBe('hello');
    service.dismiss(id);
  });

  it('when show is called with variant error, the variant is preserved', () => {
    const id = service.show('error', 'oops');
    expect(service.toasts()[0].variant).toBe('error');
    service.dismiss(id);
  });

  it('when show is called twice, two toasts appear', () => {
    const a = service.show('info', 'a');
    const b = service.show('success', 'b');
    expect(service.toasts().length).toBe(2);
    service.dismiss(a);
    service.dismiss(b);
  });

  // --- dismiss ---

  it('when dismiss is called with a valid id, the toast is removed', () => {
    const id = service.show('info', 'remove me');
    service.dismiss(id);
    expect(service.toasts().find((t) => t.id === id)).toBeUndefined();
  });

  it('when dismiss is called with a valid id, other toasts are preserved', () => {
    const a = service.show('info', 'keep');
    const b = service.show('warning', 'remove');
    service.dismiss(b);
    expect(service.toasts().find((t) => t.id === a)).toBeTruthy();
    service.dismiss(a);
  });

  it('when dismiss is called twice with the same id, count does not decrease further', () => {
    const id = service.show('info', 'once');
    service.dismiss(id);
    const countAfterFirst = service.toasts().length;
    service.dismiss(id);
    expect(service.toasts().length).toBe(countAfterFirst);
  });

  // --- auto-dismiss timer ---

  it('when a toast is shown, it is auto-dismissed after the default duration', () => {
    const id = service.show('info', 'auto', 100);
    expect(service.toasts().length).toBe(1);
    vi.advanceTimersByTime(100);
    expect(service.toasts().find((t) => t.id === id)).toBeUndefined();
  });

  it('when dismiss is called manually before the timer fires, the timer does not remove the toast again', () => {
    service.show('info', 'a', 100);
    const b = service.show('success', 'b', 100);
    service.dismiss(b);
    vi.advanceTimersByTime(100);
    expect(service.toasts().length).toBe(0);
  });
});

/* The component specs run on real timers with a duration long enough never to fire.
 * The snackbar's own enter/exit and screen-reader hand-off are timer-driven, and faking
 * the clock under them tests the fake, not the surface. Nothing here waits on those
 * timers: every assertion holds as soon as the panel is attached and ticked. */
const NEVER_EXPIRES = 600_000;

describe('ToastComponent', () => {
  let fixture: ComponentFixture<ToastComponent>;
  let host: HTMLElement;
  let service: ToastService;
  let overlay: HTMLElement;

  /** The panel lives in the CDK overlay, outside the fixture, so a fixture-local check
   *  would not reach it: tick the whole application. */
  const sync = (): void => {
    fixture.detectChanges();
    TestBed.inject(ApplicationRef).tick();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ToastComponent],
      providers: [ICON_PROVIDER],
    }).compileComponents();

    fixture = TestBed.createComponent(ToastComponent);
    host = fixture.nativeElement;
    service = TestBed.inject(ToastService);
    overlay = TestBed.inject(OverlayContainer).getContainerElement();
    sync();
  });

  afterEach(() => {
    service.toasts().forEach((t) => service.dismiss(t.id));
    sync();
  });

  // --- structure ---

  it('when created, the component renders without error', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('when no toast is pending, nothing is drawn', () => {
    expect(host.textContent).toBe('');
    expect(overlay.querySelector('.mat-mdc-snack-bar-container')).toBeNull();
  });

  // --- the Material surface ---

  it('when a toast is shown, it is drawn on a Material snackbar in the overlay', () => {
    service.show('info', 'over the page', NEVER_EXPIRES);
    sync();
    expect(overlay.querySelector('.mat-mdc-snack-bar-container')).toBeTruthy();
    expect(overlay.textContent).toContain('over the page');
    // The outlet itself stays out of the page flow — the overlay does the positioning.
    expect(host.textContent).toBe('');
  });

  it('when a toast is shown, the panel carries the hook classes global styles target', () => {
    service.show('warning', 'careful', NEVER_EXPIRES);
    sync();
    expect(overlay.querySelector('.app-toast.app-toast-warning')).toBeTruthy();
  });

  // --- polite region ---

  it('when an info toast is shown, it appears in the polite aria-live region', () => {
    service.show('info', 'hi there', NEVER_EXPIRES);
    sync();
    const polite = overlay.querySelector('[aria-live="polite"]');
    expect(polite).toBeTruthy();
    expect(overlay.textContent).toContain('hi there');
  });

  // --- assertive region ---

  it('when an error toast is shown, it appears in the assertive aria-live region', () => {
    service.show('error', 'critical!', NEVER_EXPIRES);
    sync();
    const assertive = overlay.querySelector('[aria-live="assertive"]');
    expect(assertive).toBeTruthy();
    expect(overlay.textContent).toContain('critical!');
  });

  it('when an error toast is shown, it does NOT go to a polite region', () => {
    service.show('error', 'critical!', NEVER_EXPIRES);
    sync();
    expect(overlay.querySelector('[aria-live="polite"]')).toBeNull();
  });

  // --- manual close button ---

  it('when the close button is clicked, the toast is dismissed', () => {
    service.show('success', 'bye', NEVER_EXPIRES);
    sync();
    const btn = overlay.querySelector<HTMLButtonElement>('button[aria-label]')!;
    expect(btn).toBeTruthy();
    btn.click();
    sync();
    expect(service.toasts().length).toBe(0);
  });

  it('when a close button is rendered, its label names the action', () => {
    service.show('warning', 'warn', NEVER_EXPIRES);
    sync();
    const btn = overlay.querySelector<HTMLButtonElement>('button')!;
    expect(btn.getAttribute('aria-label')).toBe('Dismiss notification');
  });

  // --- one surface, a queue behind it ---

  it('when two toasts are pending, only the oldest is on screen', () => {
    service.show('info', 'first message', NEVER_EXPIRES);
    service.show('info', 'second message', NEVER_EXPIRES);
    sync();
    expect(overlay.querySelectorAll('.app-toast').length).toBe(1);
    expect(overlay.textContent).toContain('first message');
    expect(overlay.textContent).not.toContain('second message');
  });

  it('when the toast on screen is dismissed, the next one takes its place', () => {
    const first = service.show('info', 'first message', NEVER_EXPIRES);
    service.show('success', 'second message', NEVER_EXPIRES);
    sync();
    service.dismiss(first);
    sync();
    expect(overlay.textContent).toContain('second message');
    expect(service.toasts().length).toBe(1);
  });

  // --- design tokens ---

  it('when rendered, no hardcoded hex colors appear in inline element styles', () => {
    service.show('error', 'tokens only', NEVER_EXPIRES);
    sync();
    const hexPattern = /#[0-9a-fA-F]{3,8}\b/;
    const all: HTMLElement[] = [
      host,
      ...Array.from(host.querySelectorAll('*') as NodeListOf<HTMLElement>),
      ...Array.from(overlay.querySelectorAll('*') as NodeListOf<HTMLElement>),
    ];
    for (const el of all) {
      expect(el.getAttribute('style') ?? '').not.toMatch(hexPattern);
    }
  });
});

describe('ToastComponent variants', () => {
  let fixture: ComponentFixture<ToastComponent>;
  let service: ToastService;
  let overlay: HTMLElement;

  const sync = (): void => {
    fixture.detectChanges();
    TestBed.inject(ApplicationRef).tick();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ToastComponent],
      providers: [ICON_PROVIDER],
    }).compileComponents();

    fixture = TestBed.createComponent(ToastComponent);
    service = TestBed.inject(ToastService);
    overlay = TestBed.inject(OverlayContainer).getContainerElement();
    sync();
  });

  afterEach(() => {
    service.toasts().forEach((t) => service.dismiss(t.id));
    sync();
  });

  const variants: ToastVariant[] = ['info', 'success', 'warning', 'error'];

  variants.forEach((variant) => {
    it(`when variant is ${variant}, the toast message is visible`, () => {
      service.show(variant, `${variant}-msg`, NEVER_EXPIRES);
      sync();
      expect(overlay.textContent).toContain(`${variant}-msg`);
    });
  });

  // The container is the same inverse surface for every variant, so the three that
  // carry a status say so with a glyph as well. `info` is the neutral one.
  const glyphed: ToastVariant[] = ['success', 'warning', 'error'];

  glyphed.forEach((variant) => {
    it(`when variant is ${variant}, a status glyph is drawn beside the message`, () => {
      service.show(variant, `${variant}-msg`, NEVER_EXPIRES);
      sync();
      expect(overlay.querySelector('.toast-icon')).toBeTruthy();
    });
  });

  it('when variant is info, no status glyph is drawn', () => {
    service.show('info', 'plain', NEVER_EXPIRES);
    sync();
    expect(overlay.querySelector('.toast-icon')).toBeNull();
  });
});
