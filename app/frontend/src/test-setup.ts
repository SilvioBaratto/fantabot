// Test-environment polyfills. The default jsdom environment implements neither
// window.matchMedia nor ResizeObserver, which the responsive shell and the theme
// service (and their specs, which `vi.spyOn(window, 'matchMedia')`) depend on.
// Defined configurable + writable so specs can spy on / override them.

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: (query: string): MediaQueryList =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          addListener: () => undefined,
          removeListener: () => undefined,
          dispatchEvent: () => false,
        }) as unknown as MediaQueryList,
    });
  }

  if (!('ResizeObserver' in window)) {
    (window as { ResizeObserver?: unknown }).ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    };
  }
}
