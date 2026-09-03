# Frontend

Angular single-page application (standalone components, signals, Tailwind CSS).

## Installing dependencies

Install packages with the `--legacy-peer-deps` flag:

```bash
npm install --legacy-peer-deps
```

> **Why `--legacy-peer-deps`?** `@angular/build` declares an optional peer dependency on
> `vitest@^4`, but the project pins `vitest@^3`. npm 7+ treats this mismatch as a hard
> `ERESOLVE` error and aborts `npm install`. `--legacy-peer-deps` skips npm's peer-dependency
> check and installs anyway. Safe here: `vitest` is a test-only devDependency and Angular's build
> only optionally peers it, so app build/serve are unaffected.

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
