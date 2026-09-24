// The Angular conventions in the workspace root CLAUDE.md, made enforceable.
//
// Why this file exists: an audit on 2026-09-24 measured all 113 files under `src/` and
// found *zero* violations of the five conventions — and nothing anywhere that would have
// caught the first one. `app-ci.yml` ran `ng test` and the build, both of which are happy
// with `*ngIf` and `standalone: true`. A convention that is only ever obeyed is a
// convention nobody can see break.
//
// Coverage of the five rules, stated explicitly so a gap is visible rather than assumed:
//
//   1. no explicit `standalone: true`  -> `no-restricted-syntax` below. There is NO
//      off-the-shelf rule for this: `@angular-eslint/prefer-standalone` enforces the
//      opposite direction (it flags `standalone: false`) and says nothing about the
//      redundant `true`. The selector bans the property outright in a
//      Component/Directive/Pipe decorator, in either polarity — `standalone: false` is a
//      harder violation of "standalone components only" than the redundant `true` is.
//   2. `ChangeDetectionStrategy.OnPush`  -> `prefer-on-push-component-change-detection`.
//   3. `input()`/`output()` over decorators -> `prefer-signals` (@Input, @ViewChild,
//      @ContentChild), `prefer-output-emitter-ref` (@Output), plus
//      `no-inputs-metadata-property`/`no-outputs-metadata-property` for the array form
//      that neither of those two sees.
//   4. `inject()` over constructor injection -> `prefer-inject`.
//   5. native control flow and class/style bindings -> `template/prefer-control-flow`
//      (*ngIf, *ngFor, *ngSwitch) and the template `no-restricted-syntax` block for
//      `ngClass`/`ngStyle`. `template/prefer-class-binding` is deliberately left OFF (it
//      is not in `templateRecommended` either): it exempts object keys holding
//      space-separated class names, and CLAUDE.md's rule has no exemption, so it is
//      strictly weaker than the selector below and turning it on would only double-report
//      the cases the selector already catches.
//
// Plus `@typescript-eslint/no-explicit-any` for the sixth thing the audit measured.
//
// Scope note: `src/**/*.spec.ts` gets the same TypeScript rules. Test doubles are where a
// convention erodes first, and the audit counted them among its 113 clean files.
//
// The `lint` script passes `--max-warnings 0`. One rule in `angular.configs.tsRecommended`
// ships at `warn` (`use-lifecycle-interface`), and a warning leaves eslint's exit code at
// 0 — a CI step that stays green while reporting the problem is the same thing this file
// was added to fix.

const eslint = require('@eslint/js');
const tseslint = require('typescript-eslint');
const angular = require('angular-eslint');

/**
 * The `standalone` property of a `@Component` / `@Directive` / `@Pipe` decorator, in
 * either polarity. Anchored at the Decorator and walked down through the call's object
 * literal with `>` so it cannot match a `standalone` key in some unrelated nested object
 * inside the same decorator (e.g. a `host` or `animations` entry).
 */
const STANDALONE_IN_DECORATOR =
  'Decorator[expression.callee.name=/^(Component|Directive|Pipe)$/]' +
  ' > CallExpression > ObjectExpression > Property[key.name="standalone"]';

module.exports = tseslint.config(
  {
    // Everything generated. `dist/` is the built SPA, `out-tsc/` is `tsc --outDir` from
    // the two project tsconfigs, `.angular/` is the CLI's cache.
    ignores: ['dist/**', 'out-tsc/**', '.angular/**', 'node_modules/**', 'coverage/**'],
  },
  {
    files: ['**/*.ts'],
    extends: [
      eslint.configs.recommended,
      ...tseslint.configs.recommended,
      ...angular.configs.tsRecommended,
    ],
    processor: angular.processInlineTemplates,
    rules: {
      // (1) — see the header. No rule ships for this; this selector is the rule.
      'no-restricted-syntax': [
        'error',
        {
          selector: STANDALONE_IN_DECORATOR,
          message:
            "Don't set `standalone` in a component/directive/pipe decorator: standalone is the default in Angular 21, and `standalone: false` is an NgModule component (workspace CLAUDE.md).",
        },
      ],

      // (2)
      '@angular-eslint/prefer-on-push-component-change-detection': 'error',

      // (3)
      '@angular-eslint/prefer-signals': 'error',
      '@angular-eslint/prefer-output-emitter-ref': 'error',
      '@angular-eslint/no-inputs-metadata-property': 'error',
      '@angular-eslint/no-outputs-metadata-property': 'error',

      // (4)
      '@angular-eslint/prefer-inject': 'error',

      // The sixth measured property: no `any`.
      '@typescript-eslint/no-explicit-any': 'error',

      // Selector hygiene, at the prefix `angular.json` already declares.
      '@angular-eslint/component-selector': [
        'error',
        { type: 'element', prefix: 'app', style: 'kebab-case' },
      ],
      '@angular-eslint/directive-selector': [
        'error',
        { type: 'attribute', prefix: 'app', style: 'camelCase' },
      ],
    },
  },
  {
    files: ['**/*.html'],
    extends: [...angular.configs.templateRecommended],
    rules: {
      // (5) — the control-flow half. Already on at `error` in `templateRecommended`;
      // restated here so the mapping from CLAUDE.md's five rules to the mechanism that
      // carries each is readable in one block, and so a future `templateRecommended` that
      // drops or downgrades it cannot take this rule with it.
      '@angular-eslint/template/prefer-control-flow': 'error',

      // (5) — the binding half. Hard ban, both the bound and the static-attribute form,
      // because CLAUDE.md's rule has no exemption. `prefer-class-binding` does have one
      // (and covers only `ngClass`, never `ngStyle`), which is why it cannot be what
      // carries this.
      'no-restricted-syntax': [
        'error',
        {
          selector: 'BoundAttribute[name="ngClass"], TextAttribute[name="ngClass"]',
          message: 'Use a `class` binding, not `ngClass` (workspace CLAUDE.md).',
        },
        {
          selector: 'BoundAttribute[name="ngStyle"], TextAttribute[name="ngStyle"]',
          message: 'Use a `style` binding, not `ngStyle` (workspace CLAUDE.md).',
        },
      ],
    },
  },
);
