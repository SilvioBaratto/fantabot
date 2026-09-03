import { IconName } from '../icons';

/**
 * Navigation item shared by the sidebar and the bottom-tab-bar.
 * Lives at the `shared/` root (peer to both components) so neither leaf
 * component owns the contract the other depends on.
 */
export interface NavItem {
  name: string;
  route: string;
  icon: IconName;
}

/**
 * Single source of truth for primary navigation. Shared by the desktop sidebar
 * and the mobile bottom-tab-bar so the two surfaces cannot drift out of sync.
 */
export const NAV_ITEMS: NavItem[] = [
  { name: 'Home', route: '/home', icon: 'Home' },
  { name: 'Dashboard', route: '/dashboard', icon: 'LayoutDashboard' },
  { name: 'Settings', route: '/settings', icon: 'Settings' },
];
