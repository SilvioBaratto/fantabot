import { Injectable, DestroyRef, computed, effect, inject, signal } from '@angular/core';

export type ThemeMode = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'app-theme';
const TRANSITION_MS = 150;
const NEXT: Record<ThemeMode, ThemeMode> = { system: 'light', light: 'dark', dark: 'system' };

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly destroyRef = inject(DestroyRef);

  private readonly _theme = signal<ThemeMode>(this._readStorage());
  private readonly _osPrefersDark = signal<boolean>(this._readOs());

  readonly theme = this._theme.asReadonly();
  readonly isDark = computed(
    () => this._theme() === 'dark' || (this._theme() === 'system' && this._osPrefersDark()),
  );

  constructor() {
    this._initOsListener();
    effect(() => this._applyClass(this.isDark()));
  }

  toggleTheme(): void {
    this._beginTransition();
    this.setTheme(NEXT[this._theme()]);
  }

  setTheme(theme: ThemeMode): void {
    this._theme.set(theme);
    this._persist(theme);
  }

  private _readStorage(): ThemeMode {
    if (typeof window === 'undefined') return 'system';
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
  }

  private _readOs(): boolean {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  private _initOsListener(): void {
    if (typeof window === 'undefined') return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e: MediaQueryListEvent) => this._osPrefersDark.set(e.matches);
    query.addEventListener('change', onChange);
    this.destroyRef.onDestroy(() => query.removeEventListener('change', onChange));
  }

  private _applyClass(isDark: boolean): void {
    if (typeof window === 'undefined') return;
    document.documentElement.classList.toggle('dark', isDark);
  }

  private _persist(theme: ThemeMode): void {
    if (typeof window === 'undefined') return;
    localStorage.setItem(STORAGE_KEY, theme);
  }

  private _beginTransition(): void {
    if (typeof window === 'undefined') return;
    const root = document.documentElement;
    root.classList.add('theme-transitioning');
    setTimeout(() => root.classList.remove('theme-transitioning'), TRANSITION_MS);
  }
}
