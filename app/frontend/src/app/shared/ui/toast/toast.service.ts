import { Injectable, OnDestroy, signal } from '@angular/core';

export type ToastVariant = 'info' | 'success' | 'warning' | 'error';

export interface Toast {
  id: number;
  variant: ToastVariant;
  message: string;
}

const DEFAULT_DURATION_MS = 5000;

@Injectable({ providedIn: 'root' })
export class ToastService implements OnDestroy {
  private readonly _toasts = signal<Toast[]>([]);
  private readonly _timers = new Map<number, ReturnType<typeof setTimeout>>();
  private _nextId = 0;

  readonly toasts = this._toasts.asReadonly();

  show(variant: ToastVariant, message: string, duration = DEFAULT_DURATION_MS): number {
    const id = this._nextId++;
    this._toasts.update((list) => [...list, { id, variant, message }]);
    this._scheduleAutoDismiss(id, duration);
    return id;
  }

  dismiss(id: number): void {
    this._clearTimer(id);
    this._toasts.update((list) => list.filter((t) => t.id !== id));
  }

  ngOnDestroy(): void {
    this._timers.forEach((_, id) => this._clearTimer(id));
  }

  private _scheduleAutoDismiss(id: number, duration: number): void {
    if (typeof window === 'undefined') return; // SSR guard
    const timer = setTimeout(() => this.dismiss(id), duration);
    this._timers.set(id, timer);
  }

  private _clearTimer(id: number): void {
    const timer = this._timers.get(id);
    if (timer === undefined) return;
    clearTimeout(timer);
    this._timers.delete(id);
  }
}
