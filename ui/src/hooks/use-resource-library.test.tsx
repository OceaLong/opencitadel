// @vitest-environment jsdom

import { act, useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import type { ResourceLibraryApi } from "./use-resource-library";
import { useResourceLibrary } from "./use-resource-library";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type Item = { id: string };

// A module-stable api object, mirroring `knowledgeLibraryApi` in
// `knowledge-library.tsx` (defined outside the component).
function makeApi(list: () => Promise<Item[]>): ResourceLibraryApi<Item, never> {
  return {
    list,
    remove: vi.fn(async () => {}),
    ingestStream: vi.fn(() => () => {}),
  };
}

function noop() {}

describe("useResourceLibrary load loop guard", () => {
  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("only calls api.list once across re-renders with fresh inline callbacks", async () => {
    const listSpy = vi.fn(async (): Promise<Item[]> => [{ id: "a" }]);
    const api = makeApi(listSpy);

    // Forces re-renders; each render passes brand-new inline `onReset`/`onLoaded`
    // arrows (new identity) — the exact shape that used to loop `load`.
    let bump: (() => void) | null = null;

    function Harness() {
      const [, setTick] = useState(0);
      useEffect(() => {
        bump = () => setTick((n) => n + 1);
        return () => {
          bump = null;
        };
      }, []);
      useResourceLibrary<Item, never>({
        api,
        enabled: true,
        // New references every render, on purpose.
        onReset: () => noop(),
        onLoaded: () => noop(),
        loadErrorMessage: "load failed",
        shouldPoll: () => false,
        pollMs: 5000,
        formatIngestError: () => "err",
      });
      return null;
    }

    const { unmount } = await renderComponent(<Harness />);

    // Re-render several times with new inline callback identities.
    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        bump?.();
        await Promise.resolve();
      });
    }

    expect(listSpy).toHaveBeenCalledTimes(1);
    await unmount();
  });

  it("does not re-fetch when a successful load triggers a re-render (no self-loop)", async () => {
    // Returns a new array each call so `setItems` always produces a state change,
    // which is the render that previously re-triggered `load`.
    const listSpy = vi.fn(async (): Promise<Item[]> => [{ id: "x" }]);
    const api = makeApi(listSpy);

    let observedItems: Item[] = [];

    function Harness() {
      const lib = useResourceLibrary<Item, never>({
        api,
        enabled: true,
        onReset: () => noop(),
        onLoaded: () => noop(),
        loadErrorMessage: "load failed",
        shouldPoll: () => false,
        pollMs: 5000,
        formatIngestError: () => "err",
      });
      useEffect(() => {
        observedItems = lib.items;
      }, [lib.items]);
      return null;
    }

    const { unmount } = await renderComponent(<Harness />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(observedItems).toEqual([{ id: "x" }]);
    await unmount();
  });
});
