import { IconName } from '../icons';

/**
 * A primary navigation destination, shared by the navigation bar (compact) and the
 * navigation rail (medium and up). Lives at the `shared/` root — peer to both — so
 * neither leaf component owns the contract the other depends on.
 */
export interface NavItem {
  name: string;
  route: string;
  icon: IconName;
  /**
   * Shown in the compact navigation bar. M3 caps a bar at 3–5 destinations
   * (`layout/scaffold.md:67`), and this app has ten.
   *
   * The five that are false are reached at compact through the modal rail behind the
   * menu button. **The bar and that rail must not share a destination**: listing one in
   * both gives the same place two controls at the same width, which is the drawer-beside-
   * a-bar pattern M3 does not have. `overflowItems` in the shell is literally the
   * complement of this flag, so the split cannot drift.
   */
  inBar: boolean;
}

/**
 * Single source of truth for primary navigation. The bar, the rail and the modal rail
 * all read this array, so the three surfaces cannot fall out of sync.
 */
export const NAV_ITEMS: NavItem[] = [
  { name: 'Dashboard', route: '/dashboard', icon: 'LayoutDashboard', inBar: true },
  { name: 'Asta', route: '/asta', icon: 'Sliders', inBar: true },
  { name: 'Lineup', route: '/lineup', icon: 'ClipboardList', inBar: true },
  { name: 'Prices', route: '/prices', icon: 'FunctionSquare', inBar: false },
  { name: 'Modules', route: '/modules', icon: 'CheckCircle', inBar: false },
  { name: 'Harvest', route: '/harvest', icon: 'Database', inBar: true },
  { name: 'Accounts', route: '/accounts', icon: 'User', inBar: false },
  { name: 'Synchronize', route: '/synchronize', icon: 'RefreshCw', inBar: false },
  { name: 'News', route: '/news', icon: 'MessageSquare', inBar: false },
  { name: 'System', route: '/system', icon: 'Monitor', inBar: true },
];
