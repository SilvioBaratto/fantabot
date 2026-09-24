# Frontend

Angular single-page application (standalone components, signals, Angular Material 3).

Tailwind v4 was removed on 2026-09-19 (`535917e`) together with the hand-rolled shell:
there is no `tailwindcss` dependency, no `@theme`, no `@apply` and no utility class in any
template. `src/styles/material.scss` is the app's **only** global stylesheet, and it
records the two decisions a reader would otherwise re-litigate — light-only, and the stock
azure palette.

## Installing dependencies

```bash
npm ci
```

## Development server

```bash
ng serve                    # http://localhost:4200
```

## Building

```bash
ng build                    # Production build into dist/
```

## Running unit tests

```bash
ng test
```

## Additional Resources

For the Angular CLI command reference, visit [angular.dev/tools/cli](https://angular.dev/tools/cli).
