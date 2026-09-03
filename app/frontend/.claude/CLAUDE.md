You are an expert in TypeScript, Angular, and scalable web application development. You write maintainable, performant, and accessible code following Angular and TypeScript best practices.

## TypeScript Best Practices

- Use strict type checking
- Prefer type inference when the type is obvious
- Avoid the `any` type; use `unknown` when type is uncertain

## Angular Best Practices

- Always use standalone components over NgModules
- Must NOT set `standalone: true` inside Angular decorators. It's the default.
- Use signals for state management
- Implement lazy loading for feature routes
- Do NOT use `@HostBinding`/`@HostListener`; put host bindings in the `host` object of the decorator
- Use `NgOptimizedImage` for all static images (not for inline base64)

## Components

- Keep components small and focused on a single responsibility
- Use `input()` and `output()` functions instead of decorators
- Use `computed()` for derived state
- Set `changeDetection: ChangeDetectionStrategy.OnPush` in the `@Component` decorator
- Prefer Reactive forms over Template-driven ones
- Do NOT use `ngClass`/`ngStyle`; use `class`/`style` bindings instead

## State Management

- Use signals for local component state and `computed()` for derived state
- Do NOT use `mutate` on signals; use `update` or `set`
- `effect()` is for side effects only; never copy signal→signal in an effect — use `computed()`

## Templates

- Use native control flow (`@if`, `@for`, `@switch`) instead of `*ngIf`, `*ngFor`, `*ngSwitch`
- Use the async pipe to handle observables

## Services

- Design services around a single responsibility
- Use `providedIn: 'root'` for singleton services
- Use the `inject()` function instead of constructor injection

## Security

- Never use `bypassSecurityTrust*` APIs or bind untrusted data to `[innerHTML]`; Angular's built-in sanitization is the only safe path
