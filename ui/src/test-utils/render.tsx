import { act } from "react";
import type { ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

export type RenderResult = {
  container: HTMLDivElement;
  root: Root;
  /** Unmounts the root inside `act` and detaches nothing else — pair with
   * `document.body.replaceChildren()` in `afterEach` to remove the container. */
  unmount: () => Promise<void>;
};

/**
 * Mounts `element` into a fresh `<div>` appended to `document.body` using
 * `createRoot`, wrapped in `act` so the initial render (and any effects it
 * schedules) flushes before the promise resolves.
 *
 * This is the same hand-rolled harness that used to be copy-pasted at the
 * top of nearly every `*.test.tsx` file (see
 * `src/components/session-detail-resource-version.test.tsx` prior to
 * migration), extracted so tests only need:
 *
 * ```ts
 * const { container, unmount } = await renderComponent(<MyComponent />);
 * // ...assertions against container...
 * await unmount();
 * ```
 *
 * Requires `// @vitest-environment jsdom` at the top of the test file and
 * `IS_REACT_ACT_ENVIRONMENT` to be set (handled globally by
 * `src/test-utils/setup.ts` via `vitest.config.ts`'s `test.setupFiles`).
 *
 * Tests that need to re-render the same root (e.g. to change props) can use
 * the returned `root` directly with `act(async () => { root.render(...) })`.
 */
export async function renderComponent(element: ReactElement): Promise<RenderResult> {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
    await Promise.resolve();
  });
  return {
    container,
    root,
    unmount: async () => {
      await act(async () => root.unmount());
    },
  };
}
