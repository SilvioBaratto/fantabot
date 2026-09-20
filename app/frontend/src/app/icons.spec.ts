import { LUCIDE_ICONS, LucideIconProvider } from 'lucide-angular';
import { ICON_PROVIDER } from './icons';

describe('ICON_PROVIDER', () => {
  describe('when ICON_PROVIDER is read, it is shaped for LUCIDE_ICONS multi-provider', () => {
    it('provides LUCIDE_ICONS injection token', () => {
      expect(ICON_PROVIDER.provide).toBe(LUCIDE_ICONS);
    });

    it('is registered as a multi-provider', () => {
      expect(ICON_PROVIDER.multi).toBe(true);
    });

    it('uses a LucideIconProvider instance', () => {
      expect(ICON_PROVIDER.useValue).toBeInstanceOf(LucideIconProvider);
    });
  });

  describe('when an aliased name is requested, the canonical icon is returned', () => {
    it('resolves CheckCircle to the CircleCheckBig icon data', () => {
      expect(ICON_PROVIDER.useValue.getIcon('CheckCircle')).toBeTruthy();
    });

    it('resolves Sliders to the SlidersVertical icon data', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Sliders')).toBeTruthy();
    });

    it('resolves FunctionSquare to the SquareFunction icon data', () => {
      expect(ICON_PROVIDER.useValue.getIcon('FunctionSquare')).toBeTruthy();
    });
  });

  describe('when a seed icon is requested, it is registered', () => {
    it('returns icon data for Menu', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Menu')).toBeTruthy();
    });

    it('returns icon data for MessageSquare', () => {
      expect(ICON_PROVIDER.useValue.getIcon('MessageSquare')).toBeTruthy();
    });

    it('returns icon data for LayoutDashboard', () => {
      expect(ICON_PROVIDER.useValue.getIcon('LayoutDashboard')).toBeTruthy();
    });

    it('returns icon data for User', () => {
      expect(ICON_PROVIDER.useValue.getIcon('User')).toBeTruthy();
    });

    it('returns icon data for Search', () => {
      // The backfill card's dry run (T22): look at what a run would build before writing
      // it, because a mismatched seed reports a *successful* run that loaded nothing.
      expect(ICON_PROVIDER.useValue.getIcon('Search')).toBeTruthy();
    });

    it('returns icon data for Monitor', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Monitor')).toBeTruthy();
    });

    it('returns icon data for RefreshCw', () => {
      expect(ICON_PROVIDER.useValue.getIcon('RefreshCw')).toBeTruthy();
    });

    it('returns icon data for Camera', () => {
      // Synchronize's team snapshot (T25): one capture of our own credits, appended.
      expect(ICON_PROVIDER.useValue.getIcon('Camera')).toBeTruthy();
    });

    it('returns icon data for Tags', () => {
      // Synchronize's club-name backfill (T25): three-letter codes resolved to names.
      expect(ICON_PROVIDER.useValue.getIcon('Tags')).toBeTruthy();
    });
  });

  // These names reach the provider as kebab-case string literals in templates,
  // so `IconName` cannot catch their removal — only this test can.
  describe('when a kebab-case template name is requested, it resolves', () => {
    it('returns icon data for loader-2', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Loader2')).toBeTruthy();
    });

    it('returns icon data for chevron-right', () => {
      expect(ICON_PROVIDER.useValue.getIcon('ChevronRight')).toBeTruthy();
    });

    it('returns icon data for chevron-down', () => {
      expect(ICON_PROVIDER.useValue.getIcon('ChevronDown')).toBeTruthy();
    });

    it('returns icon data for plus', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Plus')).toBeTruthy();
    });
  });
});
