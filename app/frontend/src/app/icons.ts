import { LUCIDE_ICONS, LucideIconProvider } from 'lucide-angular';
import {
  ChevronDown,
  ChevronRight,
  CircleCheckBig,
  ClipboardList,
  LayoutDashboard,
  Loader2,
  Menu,
  MessageSquare,
  Monitor,
  Plus,
  RefreshCw,
  SlidersVertical,
  SquareFunction,
  User,
  X,
} from 'lucide-angular';

// LucideIconProvider matches template names by converting kebab-case to PascalCase
// and looking up the result in this map's keys. Most entries use the icon's own
// PascalCase identifier as the key; the aliased entries below map the deprecated
// names (CheckCircle, Sliders, FunctionSquare) to their canonical
// (non-deprecated) counterparts in lucide-angular ≥ 0.477.
//
// Registered icons are exactly the ones the app renders: every entry is reachable
// either through `IconName` (type-checked) or through a kebab-case literal in a
// template (not type-checked — grep `lucide-icon` before removing one).
const icons = {
  CheckCircle: CircleCheckBig,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  FunctionSquare: SquareFunction,
  LayoutDashboard,
  Loader2,
  Menu,
  MessageSquare,
  Monitor,
  Plus,
  RefreshCw,
  Sliders: SlidersVertical,
  User,
  X,
};

export type IconName = keyof typeof icons;

export const ICON_PROVIDER = {
  provide: LUCIDE_ICONS,
  multi: true,
  useValue: new LucideIconProvider(icons),
};
