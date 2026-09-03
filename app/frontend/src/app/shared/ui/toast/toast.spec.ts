import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

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

describe('ToastComponent', () => {
  let fixture: ComponentFixture<ToastComponent>;
  let host: HTMLElement;
  let service: ToastService;

  beforeEach(async () => {
    vi.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [ToastComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(ToastComponent);
    host = fixture.nativeElement;
    service = TestBed.inject(ToastService);
    fixture.detectChanges();
  });

  afterEach(() => {
    service.toasts().forEach((t) => service.dismiss(t.id));
    vi.useRealTimers();
  });

  // --- structure ---

  it('when created, the component renders without error', () => {
    expect(fixture.componentInstance).toBeTruthy();
  });

  // --- polite region ---

  it('when an info toast is shown, it appears in the polite aria-live region', () => {
    const id = service.show('info', 'hi there');
    fixture.detectChanges();
    const polite = host.querySelector('[aria-live="polite"]');
    expect(polite?.textContent).toContain('hi there');
    service.dismiss(id);
  });

  // --- assertive region ---

  it('when an error toast is shown, it appears in the assertive aria-live region', () => {
    const id = service.show('error', 'critical!');
    fixture.detectChanges();
    const assertive = host.querySelector('[aria-live="assertive"]');
    expect(assertive?.textContent).toContain('critical!');
    service.dismiss(id);
  });

  it('when an error toast is shown, it does NOT appear in the polite region', () => {
    const id = service.show('error', 'critical!');
    fixture.detectChanges();
    const polite = host.querySelector('[aria-live="polite"]');
    expect(polite?.textContent).not.toContain('critical!');
    service.dismiss(id);
  });

  // --- manual close button ---

  it('when the close button is clicked, the toast is dismissed', () => {
    service.show('success', 'bye');
    fixture.detectChanges();
    const btn = host.querySelector<HTMLButtonElement>('button[aria-label]')!;
    expect(btn).toBeTruthy();
    btn.click();
    fixture.detectChanges();
    expect(service.toasts().length).toBe(0);
  });

  it('when a close button is rendered, it carries an aria-label', () => {
    service.show('warning', 'warn');
    fixture.detectChanges();
    const btn = host.querySelector<HTMLButtonElement>('button')!;
    expect(btn.getAttribute('aria-label')).toBeTruthy();
    service.dismiss(service.toasts()[0].id);
  });

  // --- positioning and safe-area ---

  it('when rendered, the toast container has bottom-right fixed positioning classes', () => {
    const container = host.querySelector('[aria-label="Notifications"]')!;
    expect(container.className).toContain('fixed');
    expect(container.className).toContain('bottom-');
    expect(container.className).toContain('right-');
  });

  it('when rendered, the toast container has the pb-safe class', () => {
    const container = host.querySelector('[aria-label="Notifications"]')!;
    expect(container.className).toContain('pb-safe');
  });

  // --- dark-mode tokens ---

  it('when rendered, no hardcoded hex colors appear in inline element styles', () => {
    const hexPattern = /#[0-9a-fA-F]{3,8}\b/;
    const all: HTMLElement[] = [host, ...Array.from(host.querySelectorAll('*') as NodeListOf<HTMLElement>)];
    for (const el of all) {
      expect(el.getAttribute('style') ?? '').not.toMatch(hexPattern);
    }
  });
});

describe('ToastComponent variants', () => {
  let fixture: ComponentFixture<ToastComponent>;
  let host: HTMLElement;
  let service: ToastService;

  beforeEach(async () => {
    vi.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [ToastComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(ToastComponent);
    host = fixture.nativeElement;
    service = TestBed.inject(ToastService);
    fixture.detectChanges();
  });

  afterEach(() => {
    service.toasts().forEach((t) => service.dismiss(t.id));
    vi.useRealTimers();
  });

  const variants: ToastVariant[] = ['info', 'success', 'warning', 'error'];

  variants.forEach((variant) => {
    it(`when variant is ${variant}, the toast message is visible`, () => {
      const id = service.show(variant, `${variant}-msg`);
      fixture.detectChanges();
      expect(host.textContent).toContain(`${variant}-msg`);
      service.dismiss(id);
    });
  });
});
