/**
 * Shared Vitest setup, wired in via `vitest.config.ts`'s `test.setupFiles`.
 *
 * Historically every jsdom test file toggled React's act() environment flag
 * itself, either as a bare top-level assignment or via a
 * `beforeAll`/`afterAll` pair that saved and restored the previous value
 * (see e.g. the pre-migration versions of
 * `src/components/knowledge/knowledge-library.test.tsx`). Vitest runs
 * `setupFiles` once per test file in that file's own isolated module
 * context, so setting the flag here has the same per-file scope as the
 * duplicated `beforeAll`/`afterAll` boilerplate did — there is nothing to
 * restore, because nothing outside this file's run shares the global.
 *
 * This file runs for every test file (node and jsdom alike, since
 * `// @vitest-environment jsdom` is chosen per file). Setting the flag when
 * there is no DOM/React involved is harmless.
 */
(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;
