import { LUCIDE_ICONS, LucideIconProvider } from 'lucide-angular';
import { ICON_PROVIDER } from './icons';

/**
 * Every key `icons.ts` registers — written out, deliberately, and not derived from the
 * map.
 *
 * A loop over `Object.keys(icons)` is the obvious collapse and it is the one shape this
 * test must not take: deleting an icon deletes its case too, so the assertion passes over
 * one fewer name and says nothing. That is the `default_is_stale` trap CLAUDE.md records,
 * and a spec that cannot notice a removal is the only failure this file exists for.
 *
 * Measured 2026-09-24 by renaming each key in the map, one at a time, with this file moved
 * out of the run. **Thirteen of the twenty-six fail the build** — `CheckCircle`, `CircleX`,
 * `ClipboardList`, `Database`, `FunctionSquare`, `LayoutDashboard`, `Loader2`,
 * `MessageSquare`, `Monitor`, `RefreshCw`, `Sliders`, `TriangleAlert`, `User` — because
 * they are reached through `IconName` (`shared/nav-item.ts`, `pages/synchronize`), so
 * `keyof typeof icons` catches them at compile time. **Twelve more turn a page spec red**,
 * because that page renders them. **One is caught by nothing at all: `Minus`.** It is in
 * the map, `pages/news` passes `"minus"`, and no spec renders that branch — removing it
 * shipped a blank icon and every suite stayed green.
 *
 * So the list is the whole set rather than the sixteen that used to be here: the sixteen
 * were, every one of them, already covered by the compiler or by a page spec, and the one
 * name that needed this file was not among them.
 */
const REGISTERED = [
  'Camera',
  'CheckCircle',
  'ChevronDown',
  'ChevronRight',
  'CircleMinus',
  'CircleX',
  'ClipboardList',
  'Database',
  'FunctionSquare',
  'Gavel',
  'LayoutDashboard',
  'Loader2',
  'Menu',
  'MessageSquare',
  'Minus',
  'Monitor',
  'Plus',
  'RefreshCw',
  'Search',
  'Sliders',
  'Tags',
  'TrendingDown',
  'TrendingUp',
  'TriangleAlert',
  'User',
  'X',
];

/**
 * ⚠ What is **not** here, and why, because the obvious next test does not work.
 *
 * `icons.ts` says "`LucideIconProvider` matches template names by converting kebab-case to
 * PascalCase". Measured 2026-09-24 against `lucide-angular@0.577`: it does not.
 * `LucideIconProvider.getIcon` is a plain map lookup, and the conversion lives on the
 * **component** — `LucideAngularComponent.getIcon(this.toPascalCase(nameOrIcon))`. A test
 * that hands `getIcon` the nineteen kebab-case spellings the templates use was written
 * here first and every one of them came back `null`, which is the provider answering
 * correctly about an input it never receives.
 *
 * So the four tests this file used to carry under the heading *"when a kebab-case template
 * name is requested, it resolves"* were titled `loader-2`, `chevron-right`, `chevron-down`
 * and `plus` while calling `getIcon('Loader2')`, `getIcon('ChevronRight')`,
 * `getIcon('ChevronDown')`, `getIcon('Plus')`. The **bodies were right** and the titles
 * were not: Pascal is what the provider is asked. They are covered by `REGISTERED` now,
 * under their real names.
 *
 * Re-spelling that check honestly would mean copying `toPascalCase`'s regex into the spec
 * — a second answer that can disagree with the first — or rendering `<lucide-icon>` in a
 * TestBed, which the page specs already do for twelve of these names. Neither is worth a
 * test here; what is recorded instead is that the conversion is the component's, so nobody
 * writes the `getIcon('loader-2')` test a third time.
 */

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

  it('resolves every icon it registers, including the ones no page spec renders', () => {
    const missing = REGISTERED.filter((name) => !ICON_PROVIDER.useValue.getIcon(name));

    expect(missing).toEqual([]);
  });
});
