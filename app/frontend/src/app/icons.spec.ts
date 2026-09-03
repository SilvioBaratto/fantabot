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

    it('returns icon data for Home', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Home')).toBeTruthy();
    });

    it('returns icon data for LayoutDashboard', () => {
      expect(ICON_PROVIDER.useValue.getIcon('LayoutDashboard')).toBeTruthy();
    });

    it('returns icon data for User', () => {
      expect(ICON_PROVIDER.useValue.getIcon('User')).toBeTruthy();
    });

    it('returns icon data for LogOut', () => {
      expect(ICON_PROVIDER.useValue.getIcon('LogOut')).toBeTruthy();
    });

    it('returns icon data for Sun', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Sun')).toBeTruthy();
    });

    it('returns icon data for Moon', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Moon')).toBeTruthy();
    });

    it('returns icon data for Monitor', () => {
      expect(ICON_PROVIDER.useValue.getIcon('Monitor')).toBeTruthy();
    });
  });
});
