// @vitest-environment jsdom

import { act, useEffect } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../messages/en.json";
import {
  type PaginatedFetcher,
  usePaginatedList,
  type UsePaginatedListResult,
} from "./use-paginated-list";

type Item = { id: string };

/**
 * Renders the hook and mirrors its latest result into `sinkRef`, plus fires
 * `load(0)` once on mount (matching how the admin pages bootstrap).
 */
function Harness({
  fetcher,
  pageSize,
  sinkRef,
}: {
  fetcher: PaginatedFetcher<Item>;
  pageSize?: number;
  sinkRef: { current: UsePaginatedListResult<Item> | null };
}) {
  const result = usePaginatedList<Item>(fetcher, pageSize ? { pageSize } : undefined);
  const { load } = result;
  // Mirror the latest hook result out of the component after each commit.
  useEffect(() => {
    sinkRef.current = result;
  });
  useEffect(() => {
    void load(0);
  }, [load]);
  return null;
}

/** Renders the harness under the intl provider the hook now requires. */
function renderHarness(props: Parameters<typeof Harness>[0]) {
  return renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <Harness {...props} />
    </NextIntlClientProvider>,
  );
}

describe("usePaginatedList", () => {
  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("loads the first page and derives paging metadata", async () => {
    const fetcher = vi.fn(async ({ limit, offset }: { limit: number; offset: number }) => ({
      items: [{ id: `${offset}` }],
      total: 45,
      _seen: { limit, offset },
    }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderHarness({ fetcher, sinkRef });

    expect(fetcher).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(sinkRef.current?.items).toEqual([{ id: "0" }]);
    expect(sinkRef.current?.total).toBe(45);
    expect(sinkRef.current?.totalPages).toBe(3);
    expect(sinkRef.current?.currentPage).toBe(1);
    expect(sinkRef.current?.canPrev).toBe(false);
    expect(sinkRef.current?.canNext).toBe(true);
    expect(sinkRef.current?.loading).toBe(false);

    await unmount();
  });

  it("advances and rewinds pages by pageSize", async () => {
    const fetcher = vi.fn(async ({ offset }: { limit: number; offset: number }) => ({
      items: [{ id: `${offset}` }],
      total: 45,
    }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderHarness({ fetcher, sinkRef });

    await act(async () => {
      await sinkRef.current?.nextPage();
    });
    expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(sinkRef.current?.offset).toBe(20);
    expect(sinkRef.current?.currentPage).toBe(2);
    expect(sinkRef.current?.canPrev).toBe(true);

    await act(async () => {
      await sinkRef.current?.prevPage();
    });
    expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
    expect(sinkRef.current?.offset).toBe(0);

    await unmount();
  });

  it("keeps prior state when the fetcher returns null (handled error)", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      return call === 1 ? { items: [{ id: "a" }], total: 10 } : null;
    });
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderHarness({ fetcher, sinkRef });
    expect(sinkRef.current?.items).toEqual([{ id: "a" }]);

    await act(async () => {
      await sinkRef.current?.load(20);
    });
    // Failed fetch: list + offset unchanged, loading settled back to false.
    expect(sinkRef.current?.items).toEqual([{ id: "a" }]);
    expect(sinkRef.current?.offset).toBe(0);
    expect(sinkRef.current?.loading).toBe(false);
    // fetcher 自行处理过的失败不会触发 hook 的 error。
    expect(sinkRef.current?.error).toBeNull();

    await unmount();
  });

  it("catches a throwing fetcher, exposes error and keeps prior state", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      if (call === 1) return { items: [{ id: "a" }], total: 10 };
      throw new Error("boom");
    });
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderHarness({ fetcher, sinkRef });
    expect(sinkRef.current?.error).toBeNull();

    await act(async () => {
      await sinkRef.current?.load(20);
    });
    expect(sinkRef.current?.error).toBe("boom");
    expect(sinkRef.current?.items).toEqual([{ id: "a" }]);
    expect(sinkRef.current?.offset).toBe(0);
    expect(sinkRef.current?.loading).toBe(false);

    // A later successful load clears the error.
    call = 0;
    await act(async () => {
      await sinkRef.current?.load(0);
    });
    expect(sinkRef.current?.error).toBeNull();

    await unmount();
  });

  it("honours a custom pageSize", async () => {
    const fetcher = vi.fn(async () => ({ items: [], total: 25 }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderHarness({ fetcher, pageSize: 10, sinkRef });

    expect(fetcher).toHaveBeenCalledWith({ limit: 10, offset: 0 });
    expect(sinkRef.current?.totalPages).toBe(3);

    await unmount();
  });
});
