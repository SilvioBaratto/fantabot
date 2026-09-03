import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { ThemeService } from './theme';

const STORAGE_KEY = 'app-theme';

describe('ThemeService', () => {
  let matchMediaMock: {
    matches: boolean;
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
  };

  function setupMatchMedia(prefersDark: boolean): void {
    matchMediaMock = {
      matches: prefersDark,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    vi.spyOn(window, 'matchMedia').mockReturnValue(matchMediaMock as unknown as MediaQueryList);
  }

  function createService(): ThemeService {
    TestBed.configureTestingModule({});
    return TestBed.inject(ThemeService);
  }

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove('dark', 'theme-transitioning');
  });

  describe('isDark computed', () => {
    it('when theme is light, false is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'light');
      setupMatchMedia(true);
      expect(createService().isDark()).toBe(false);
    });

    it('when theme is dark, true is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'dark');
      setupMatchMedia(false);
      expect(createService().isDark()).toBe(true);
    });

    it('when theme is system and OS prefers dark, true is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'system');
      setupMatchMedia(true);
      expect(createService().isDark()).toBe(true);
    });

    it('when theme is system and OS prefers light, false is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'system');
      setupMatchMedia(false);
      expect(createService().isDark()).toBe(false);
    });
  });

  describe('initialization', () => {
    it('when no theme is stored, system is returned as the fallback', () => {
      setupMatchMedia(false);
      expect(createService().theme()).toBe('system');
    });
  });

  describe('toggleTheme cycle', () => {
    it('when theme is system, light is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'system');
      setupMatchMedia(false);
      const service = createService();
      service.toggleTheme();
      expect(service.theme()).toBe('light');
    });

    it('when theme is light, dark is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'light');
      setupMatchMedia(false);
      const service = createService();
      service.toggleTheme();
      expect(service.theme()).toBe('dark');
    });

    it('when theme is dark, system is returned', () => {
      localStorage.setItem(STORAGE_KEY, 'dark');
      setupMatchMedia(false);
      const service = createService();
      service.toggleTheme();
      expect(service.theme()).toBe('system');
    });
  });

  describe('setTheme persistence', () => {
    it('when setTheme is called, the choice is written to localStorage', () => {
      setupMatchMedia(false);
      const service = createService();
      service.setTheme('dark');
      // Verify via read-back — avoids jsdom localStorage spy quirks
      expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
    });
  });

  describe('dark class application', () => {
    it('when isDark is true, the dark class is present on documentElement', () => {
      localStorage.setItem(STORAGE_KEY, 'dark');
      setupMatchMedia(false);
      createService();
      TestBed.tick();
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('when isDark is false, the dark class is absent from documentElement', () => {
      localStorage.setItem(STORAGE_KEY, 'light');
      setupMatchMedia(true);
      createService();
      TestBed.tick();
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });
  });

  describe('OS preference listener', () => {
    it('when the service initializes, a matchMedia change listener is registered', () => {
      setupMatchMedia(false);
      createService();
      expect(matchMediaMock.addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    });
  });

  describe('SSR safety', () => {
    it('when the service is constructed, no error is thrown', () => {
      setupMatchMedia(false);
      expect(() => createService()).not.toThrow();
    });
  });
});
