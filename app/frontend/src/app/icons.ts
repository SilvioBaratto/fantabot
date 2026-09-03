import { LUCIDE_ICONS, LucideIconProvider } from 'lucide-angular';
import {
  ArrowUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleCheckBig,
  House,
  Info,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  Search,
  Settings,
  Settings2,
  SlidersVertical,
  SquareFunction,
  Sun,
  User,
  X,
} from 'lucide-angular';

// LucideIconProvider matches template names by converting kebab-case to PascalCase
// and looking up the result in this map's keys. Most entries use the icon's own
// PascalCase identifier as the key; the aliased entries below map the deprecated
// names (CheckCircle, Sliders, FunctionSquare, Home) to their canonical
// (non-deprecated) counterparts in lucide-angular ≥ 0.477.
const icons = {
  ArrowUp,
  CheckCircle: CircleCheckBig,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FunctionSquare: SquareFunction,
  Home: House,
  Info,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  Moon,
  Plus,
  Search,
  Settings,
  Settings2,
  Sliders: SlidersVertical,
  Sun,
  User,
  X,
};

export type IconName = keyof typeof icons;

export const ICON_PROVIDER = {
  provide: LUCIDE_ICONS,
  multi: true,
  useValue: new LucideIconProvider(icons),
};
